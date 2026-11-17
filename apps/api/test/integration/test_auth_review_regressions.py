from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.users import CreateUserInput, UpdateUserInput
from app.application.security import Principal, Role
from app.domain.entities.operations import Incident
from app.domain.entities.user import UserRole
from app.domain.exceptions.errors import DomainValidationError
from app.domain.value_objects.enums import Severity
from app.infrastructure.persistence.database import SqliteDatabase
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.api.test.integration.test_auth_users_rbac import _login, _make_enforced_app


def _create_examiner(client, headers, username="reviewex"):
    response = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "username": username,
            "password": "OldPass123!",
            "full_name": "Review Examiner",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize("change", ["password", "lock_unlock", "role"])
@pytest.mark.parametrize("legacy_token", [False, True])
def test_security_updates_revoke_http_refresh_and_websocket_tokens(tmp_path, change, legacy_token):
    app = _make_enforced_app(tmp_path)
    with TestClient(app) as client:
        admin_headers = {"Authorization": f"Bearer {_login(client, 'rootadmin', 'AdminPass123!')}"}
        user_id = _create_examiner(client, admin_headers)
        old_token = _login(client, "reviewex", "OldPass123!")
        if legacy_token:
            old_token = app.state.token_service.issue(
                Principal("reviewex", Role.EXAMINER, frozenset())
            )
        old_headers = {"Authorization": f"Bearer {old_token}"}
        payload = {
            "password": {"password": "NewPass123!"},
            "lock_unlock": {"is_active": False},
            "role": {"role": "ADMIN"},
        }[change]
        with client.websocket_connect(f"/api/v1/ws?token={old_token}") as ws:
            assert ws.receive_json()["type"] == "connected"
            assert (
                client.patch(
                    f"/api/v1/users/{user_id}", headers=admin_headers, json=payload
                ).status_code
                == 200
            )
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 1008
        if change == "lock_unlock":
            assert (
                client.patch(
                    f"/api/v1/users/{user_id}", headers=admin_headers, json={"is_active": True}
                ).status_code
                == 200
            )
        assert client.get("/api/v1/auth/me", headers=old_headers).status_code == 401
        assert client.post("/api/v1/auth/refresh", headers=old_headers).status_code == 401
        password = "NewPass123!" if change == "password" else "OldPass123!"
        if change == "password":
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": "reviewex", "password": "OldPass123!"}
                ).status_code
                == 401
            )
        fresh_token = _login(client, "reviewex", password)
        refreshed = client.post(
            "/api/v1/auth/refresh", headers={"Authorization": f"Bearer {fresh_token}"}
        )
        assert refreshed.status_code == 200
        refreshed_token = refreshed.json()["access_token"]
        assert app.state.token_service.verify(refreshed_token).auth_version > 0
        assert (
            client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {refreshed_token}"}
            ).status_code
            == 200
        )


def test_display_name_update_keeps_token_valid(tmp_path):
    app = _make_enforced_app(tmp_path)
    with TestClient(app) as client:
        token = _login(client, "rootadmin", "AdminPass123!")
        headers = {"Authorization": f"Bearer {token}"}
        user_id = client.get("/api/v1/auth/me", headers=headers).json()["user"]["id"]
        assert (
            client.patch(
                f"/api/v1/users/{user_id}", headers=headers, json={"full_name": "Updated Name"}
            ).status_code
            == 200
        )
        response = client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 200
        assert response.json()["user"]["full_name"] == "Updated Name"


@pytest.mark.parametrize("invalid_examiner", ["missing", "admin", "inactive"])
def test_invalid_pipeline_examiner_rolls_back_all_session_data(tmp_path, invalid_examiner):
    app = _make_enforced_app(tmp_path)
    app.state.container.register_agent(RegisterAgentInput("PC-REVIEW", "PC", "10.0.0.1", "2.0"))
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {_login(client, 'rootadmin', 'AdminPass123!')}"}
        examiner_id = "USR-MISSING"
        expected_status = 404
        if invalid_examiner == "admin":
            examiner_id = client.get("/api/v1/auth/me", headers=headers).json()["user"]["id"]
            expected_status = 422
        elif invalid_examiner == "inactive":
            examiner_id = _create_examiner(client, headers)
            client.patch(f"/api/v1/users/{examiner_id}", headers=headers, json={"is_active": False})
            expected_status = 422
        with app.state.container.database.unit_of_work() as uow:
            before_audits = uow.audits.list_for_session(None)
        response = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "exam_name": "Must Roll Back",
                "room_id": "LAB",
                "gateway_id": "GW-REVIEW",
                "workstation_ids": ["PC-REVIEW"],
                "examiner_ids": [examiner_id],
            },
        )
        assert response.status_code == expected_status
        assert client.get("/api/v1/sessions", headers=headers).json() == []
        with app.state.container.database.unit_of_work() as uow:
            assert uow.commands.list_for_target("PC-REVIEW") == []
            assert uow.audits.list_for_session(None) == before_audits
        connection = app.state.container.database.connect()
        try:
            for table in ("exam_sessions", "session_workstations", "session_examiners"):
                assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE session_id IS NOT NULL"
                ).fetchone()[0]
                == 0
            )
        finally:
            connection.close()


def test_pipeline_creation_assigns_examiners_and_audit_atomically(tmp_path):
    app = _make_enforced_app(tmp_path)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {_login(client, 'rootadmin', 'AdminPass123!')}"}
        examiner_id = _create_examiner(client, headers)
        created = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "exam_name": "Valid Pipeline",
                "room_id": "LAB",
                "gateway_id": "GW-REVIEW",
                "workstation_ids": ["PC-REVIEW"],
                "examiner_ids": [examiner_id, examiner_id],
            },
        )
        assert created.status_code == 201
        assert [user["id"] for user in created.json()["examiners"]] == [examiner_id]
        session_id = created.json()["id"]
        with app.state.container.database.unit_of_work() as uow:
            assert uow.users.list_session_ids_for_user(examiner_id) == frozenset({session_id})
            audits = uow.audits.list_for_session(session_id)
            assert [event.action for event in audits] == [
                "SESSION_CREATED",
                "SESSION_EXAMINERS_UPDATED",
            ]
            assert uow.audits.verify_chain(session_id)


@pytest.mark.parametrize("has_allowed_incident", [False, True])
def test_agent_incidents_are_selected_within_examiner_scope(tmp_path, has_allowed_incident):
    app = _make_enforced_app(tmp_path)
    app.state.container.register_agent(RegisterAgentInput("REUSED-PC", "PC", "10.0.0.2", "2.0"))
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {_login(client, 'rootadmin', 'AdminPass123!')}"}
        examiner_id = _create_examiner(client, headers)
        first = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={
                "name": "Allowed",
                "room": "LAB",
                "agent_ids": ["REUSED-PC"],
                "examiner_ids": [examiner_id],
            },
        ).json()
        assert (
            client.patch(
                f"/api/v1/sessions/{first['id']}/status",
                headers=headers,
                json={"status": "FINISHED"},
            ).status_code
            == 200
        )
        second = client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"name": "Private", "room": "LAB", "agent_ids": ["REUSED-PC"]},
        ).json()
        with app.state.container.database.unit_of_work() as uow:
            if has_allowed_incident:
                uow.incidents.add(
                    Incident(
                        session_id=first["id"],
                        workstation_id="REUSED-PC",
                        category="ALLOWED",
                        severity=Severity.WARNING,
                        evidence={},
                        created_at=datetime.now(UTC) - timedelta(minutes=1),
                    )
                )
            private = Incident(
                session_id=second["id"],
                workstation_id="REUSED-PC",
                category="PRIVATE",
                severity=Severity.CRITICAL,
                evidence={},
            )
            uow.incidents.add(private)
            assert uow.incidents.latest_by_workstation(session_ids=frozenset()) == {}
            uow.commit()
        ex_headers = {"Authorization": f"Bearer {_login(client, 'reviewex', 'OldPass123!')}"}
        assert client.get(f"/api/v1/sessions/{second['id']}", headers=ex_headers).status_code == 403
        visible = client.get("/api/v1/agents", headers=ex_headers).json()[0]["latest_incident"]
        if has_allowed_incident:
            assert visible["session_id"] == first["id"]
        else:
            assert visible is None
        assert (
            client.get("/api/v1/agents", headers=headers).json()[0]["latest_incident"]["id"]
            == private.id
        )


def test_last_admin_cannot_demote_self_through_api(tmp_path):
    app = _make_enforced_app(tmp_path)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {_login(client, 'rootadmin', 'AdminPass123!')}"}
        admin_id = client.get("/api/v1/auth/me", headers=headers).json()["user"]["id"]
        response = client.patch(
            f"/api/v1/users/{admin_id}", headers=headers, json={"role": "EXAMINER"}
        )
        assert response.status_code == 422
        assert "last active administrator" in response.json()["detail"]
        assert client.get("/api/v1/users", headers=headers).status_code == 200


def test_concurrent_demotions_keep_one_active_administrator(tmp_path):
    app = _make_enforced_app(tmp_path)
    container = app.state.container
    other = container.create_user(
        CreateUserInput("otheradmin", "OtherPass123!", "Other", UserRole.ADMIN)
    )
    with container.database.unit_of_work() as uow:
        original = uow.users.find_by_username("rootadmin")

    def demote(user_id):
        try:
            container.update_user(UpdateUserInput(user_id=user_id, role=UserRole.EXAMINER))
            return True
        except DomainValidationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(demote, [original.id, other.id]))
    assert sorted(results) == [False, True]
    with container.database.unit_of_work() as uow:
        remaining = uow.users.list_all(role=UserRole.ADMIN, active_only=True)
    assert len(remaining) == 1
    with pytest.raises(DomainValidationError, match="last active administrator"):
        container.update_user(UpdateUserInput(user_id=remaining[0].id, is_active=False))


def test_existing_sqlite_user_migrates_without_losing_identity(tmp_path):
    app = _make_enforced_app(tmp_path)
    database = app.state.container.database
    connection = database.connect()
    try:
        connection.execute("ALTER TABLE users DROP COLUMN auth_version")
        connection.commit()
    finally:
        connection.close()
    restarted = SqliteDatabase(database.path)
    restarted.initialize()
    restarted.initialize()
    with restarted.unit_of_work() as uow:
        user = uow.users.find_by_username("rootadmin")
        assert user.auth_version == 0
        assert user.role == UserRole.ADMIN
