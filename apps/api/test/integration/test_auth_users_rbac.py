from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.gateways import BindAgentToGatewayInput, RegisterGatewayInput
from app.config import Settings
from app.infrastructure.security import TokenService, hash_password
from app.main import create_app
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

SIGNING_KEY = "auth-rbac-integration-signing-key-32-chars"


def _login(client: TestClient, username: str, password: str) -> str:
    res = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert res.status_code == 200
    return res.json()["access_token"]


def _make_enforced_app(
    tmp_path, *, enforce_auth: bool = True, admin_password: str = "AdminPass123!"
):
    app = create_app(tmp_path / "rbac.db")
    app.state.settings = replace(
        app.state.settings,
        environment="development",
        enforce_auth=enforce_auth,
        auth_signing_key=SIGNING_KEY,
        examiner_username="rootadmin",
        examiner_password_hash=hash_password(admin_password, salt=b"0123456789abcdef"),
        examiner_role="ADMIN",
        token_lifetime_seconds=3600,
    )
    app.state.token_service = TokenService(SIGNING_KEY, lifetime_seconds=3600)
    from app.application.use_cases.users.management import ensure_bootstrap_user

    ensure_bootstrap_user(
        app.state.container.database.unit_of_work,
        username=app.state.settings.examiner_username,
        password_hash=app.state.settings.examiner_password_hash,
        role=app.state.settings.examiner_role,
    )
    return app


def test_auth_me_in_unenforced_development_mode(tmp_path) -> None:
    app = create_app(tmp_path / "dev.db")
    with TestClient(app) as client:
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json() == {
            "authenticated": False,
            "auth_enforced": False,
            "user": None,
        }


def test_enforce_auth_flag_and_token_lifecycle_logout_and_refresh(tmp_path) -> None:
    app = _make_enforced_app(tmp_path)
    with TestClient(app) as client:
        unauth = client.get("/api/v1/sessions")
        assert unauth.status_code == 401

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "rootadmin", "password": "AdminPass123!"},
        )
        assert login.status_code == 200
        body = login.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["username"] == "rootadmin"
        assert body["user"]["role"] == "ADMIN"
        token = body["access_token"]

        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["authenticated"] is True
        assert me.json()["auth_enforced"] is True
        assert me.json()["user"]["username"] == "rootadmin"

        refreshed = client.post(
            "/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert refreshed.status_code == 200
        new_token = refreshed.json()["access_token"]
        assert new_token != token

        logout = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert logout.status_code == 200
        assert logout.json()["status"] == "logged_out"

        after_logout = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert after_logout.status_code == 401

        refresh_revoked = client.post(
            "/api/v1/auth/refresh",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert refresh_revoked.status_code == 401


def test_user_management_dynamic_session_scope_and_account_locking(tmp_path) -> None:
    app = _make_enforced_app(tmp_path)
    app.state.container.register_agent(
        RegisterAgentInput("PC-01", "PC-01", "10.0.0.10", "2.0")
    )
    app.state.container.register_agent(
        RegisterAgentInput("PC-02", "PC-02", "10.0.0.11", "2.0")
    )

    with TestClient(app) as client:
        admin_token = client.post(
            "/api/v1/auth/login",
            json={"username": "rootadmin", "password": "AdminPass123!"},
        ).json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Admin creates an Examiner
        created_user = client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={
                "username": "examiner01",
                "password": "ExaminerPass123!",
                "full_name": "Nguyen Van Giam Thi",
                "role": "EXAMINER",
            },
        )
        assert created_user.status_code == 201
        examiner_id = created_user.json()["id"]

        # Admin creates two sessions, assigning examiner01 only to session 1
        ses1 = client.post(
            "/api/v1/sessions",
            headers=admin_headers,
            json={
                "name": "Mon Toan",
                "room": "A101",
                "agent_ids": ["PC-01"],
                "examiner_ids": [examiner_id],
            },
        )
        assert ses1.status_code == 201
        ses1_id = ses1.json()["id"]
        assert len(ses1.json()["examiners"]) == 1
        assert ses1.json()["examiners"][0]["username"] == "examiner01"

        ses2 = client.post(
            "/api/v1/sessions",
            headers=admin_headers,
            json={
                "name": "Mon Ly",
                "room": "A102",
                "agent_ids": ["PC-02"],
                "examiner_ids": [],
            },
        )
        assert ses2.status_code == 201
        ses2_id = ses2.json()["id"]

        # Verify real actor attribution in audit trail (rootadmin, not "teacher")
        with app.state.container.database.unit_of_work() as uow:
            audits = uow.audits.list_for_session(ses1_id)
            assert any(
                event.actor == "rootadmin" and event.action == "SESSION_CREATED"
                for event in audits
            )

        # Examiner logs in
        ex_token = client.post(
            "/api/v1/auth/login",
            json={"username": "examiner01", "password": "ExaminerPass123!"},
        ).json()["access_token"]
        ex_headers = {"Authorization": f"Bearer {ex_token}"}

        # Examiner cannot create users, create sessions, or manage policy profiles
        assert client.get("/api/v1/users", headers=ex_headers).status_code == 403
        assert (
            client.post(
                "/api/v1/sessions",
                headers=ex_headers,
                json={"name": "Hack", "room": "X", "agent_ids": ["PC-01"]},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/policy-profiles",
                headers=ex_headers,
                json={
                    "id": "NOPE",
                    "label": "Nope",
                    "description": "Nope",
                    "rules": {},
                },
            ).status_code
            == 403
        )

        # Examiner only lists session 1 and cannot view session 2
        visible_sessions = client.get("/api/v1/sessions", headers=ex_headers).json()
        assert [s["id"] for s in visible_sessions] == [ses1_id]
        assert client.get(f"/api/v1/sessions/{ses1_id}", headers=ex_headers).status_code == 200
        assert client.get(f"/api/v1/sessions/{ses2_id}", headers=ex_headers).status_code == 403

        # Admin dynamically reassigns examiner01 from ses1 -> ses2; existing token reflects change
        reassign1 = client.put(
            f"/api/v1/sessions/{ses1_id}/examiners",
            headers=admin_headers,
            json={"examiner_ids": []},
        )
        assert reassign1.status_code == 200
        reassign2 = client.put(
            f"/api/v1/sessions/{ses2_id}/examiners",
            headers=admin_headers,
            json={"examiner_ids": [examiner_id]},
        )
        assert reassign2.status_code == 200

        assert client.get(f"/api/v1/sessions/{ses1_id}", headers=ex_headers).status_code == 403
        assert client.get(f"/api/v1/sessions/{ses2_id}", headers=ex_headers).status_code == 200

        # Admin locks examiner01 -> existing token immediately returns 401 and login fails
        lock = client.patch(
            f"/api/v1/users/{examiner_id}",
            headers=admin_headers,
            json={"is_active": False},
        )
        assert lock.status_code == 200
        assert lock.json()["is_active"] is False
        assert client.get("/api/v1/sessions", headers=ex_headers).status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": "examiner01", "password": "ExaminerPass123!"},
            ).status_code
            == 401
        )


def test_settings_enforce_auth_requires_signing_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EECP_DATABASE_PATH", str(tmp_path / "cfg.db"))
    monkeypatch.setenv("EECP_ENVIRONMENT", "development")
    monkeypatch.setenv("EECP_ENFORCE_AUTH", "true")
    monkeypatch.delenv("EECP_AUTH_SIGNING_KEY", raising=False)
    try:
        Settings.from_env()
    except ValueError as exc:
        assert "EECP_AUTH_SIGNING_KEY" in str(exc)
    else:  # pragma: no cover
        raise AssertionError(
            "expected ValueError when EECP_ENFORCE_AUTH is enabled without signing key"
        )


def test_expired_token_still_rejected(tmp_path) -> None:
    app = _make_enforced_app(tmp_path)
    from app.application.security import Principal, Role

    expired = app.state.token_service.issue(
        Principal("examiner", Role.EXAMINER, frozenset()),
        now=datetime.now(UTC) - timedelta(hours=2),
    )
    with TestClient(app) as client:
        res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
        assert res.status_code == 401


def test_agent_http_registration_requires_admin_and_blocks_production_like(tmp_path) -> None:
    app = _make_enforced_app(tmp_path)
    payload = {
        "id": "PC90",
        "hostname": "HOST-90",
        "ip_address": "10.0.0.90",
        "agent_version": "2.0.0",
    }
    with TestClient(app) as client:
        admin_token = _login(client, "rootadmin", "AdminPass123!")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        ex_res = client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={
                "username": "examiner_reg",
                "password": "ExaminerPass123!",
                "full_name": "Examiner",
                "role": "EXAMINER",
            },
        )
        assert ex_res.status_code == 201
        ex_token = _login(client, "examiner_reg", "ExaminerPass123!")
        ex_headers = {"Authorization": f"Bearer {ex_token}"}

        assert client.post("/api/v1/agents/register", json=payload).status_code == 401
        assert (
            client.post("/api/v1/agents/register", headers=ex_headers, json=payload).status_code
            == 403
        )
        assert (
            client.post("/api/v1/agents/register", headers=admin_headers, json=payload).status_code
            == 201
        )
        assert client.post("/api/v1/agents/PC90/heartbeat").status_code == 401
        assert (
            client.post("/api/v1/agents/PC90/heartbeat", headers=ex_headers).status_code == 403
        )
        assert (
            client.post("/api/v1/agents/PC90/heartbeat", headers=admin_headers).status_code == 200
        )

        app.state.settings = replace(app.state.settings, environment="production-like")
        assert (
            client.post("/api/v1/agents/register", headers=admin_headers, json=payload).status_code
            == 403
        )
        assert (
            client.post("/api/v1/agents/PC90/heartbeat", headers=admin_headers).status_code == 403
        )


def test_examiner_scope_filters_agents_gateways_commands_and_closes_ws_on_lock(tmp_path) -> None:
    app = _make_enforced_app(tmp_path)
    app.state.container.register_agent(
        RegisterAgentInput("PC-S1", "HOST-S1", "10.0.1.1", "2.0.0")
    )
    app.state.container.register_agent(
        RegisterAgentInput("PC-S2", "HOST-S2", "10.0.1.2", "2.0.0")
    )
    app.state.container.register_gateway(RegisterGatewayInput("GW-S1", "A101", "2.0.0"))
    app.state.container.register_gateway(RegisterGatewayInput("GW-S2", "A102", "2.0.0"))
    app.state.container.bind_agent_to_gateway(BindAgentToGatewayInput("PC-S1", "GW-S1"))
    app.state.container.bind_agent_to_gateway(BindAgentToGatewayInput("PC-S2", "GW-S2"))

    with TestClient(app) as client:
        admin_token = _login(client, "rootadmin", "AdminPass123!")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        ex_res = client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={
                "username": "ex_scope",
                "password": "ExaminerPass123!",
                "full_name": "Scoped Examiner",
                "role": "EXAMINER",
            },
        )
        assert ex_res.status_code == 201
        ex_id = ex_res.json()["id"]

        ses1 = client.post(
            "/api/v1/sessions",
            headers=admin_headers,
            json={
                "name": "Scoped Session 1",
                "room": "A101",
                "agent_ids": ["PC-S1"],
                "examiner_ids": [ex_id],
            },
        )
        ses2 = client.post(
            "/api/v1/sessions",
            headers=admin_headers,
            json={
                "name": "Other Session 2",
                "room": "A102",
                "agent_ids": ["PC-S2"],
            },
        )
        assert ses1.status_code == 201
        assert ses2.status_code == 201

        ex_token = _login(client, "ex_scope", "ExaminerPass123!")
        ex_headers = {"Authorization": f"Bearer {ex_token}"}

        agents = client.get("/api/v1/agents", headers=ex_headers)
        assert agents.status_code == 200
        assert [a["id"] for a in agents.json()] == ["PC-S1"]

        gateways = client.get("/api/v2/gateways", headers=ex_headers)
        assert gateways.status_code == 200
        assert [g["gateway_id"] for g in gateways.json()] == ["GW-S1"]

        assert (
            client.get("/api/v2/gateways/agents/PC-S1", headers=ex_headers).status_code == 200
        )
        assert (
            client.get("/api/v2/gateways/agents/PC-S2", headers=ex_headers).status_code == 403
        )
        assert (
            client.get("/api/v1/agents/PC-S1/commands", headers=ex_headers).status_code == 200
        )
        assert (
            client.get("/api/v1/agents/PC-S2/commands", headers=ex_headers).status_code == 403
        )

        # Open WS for scoped examiner, then lock account -> WS disconnects with 1008
        with client.websocket_connect(f"/api/v1/ws?token={ex_token}") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            locked = client.patch(
                f"/api/v1/users/{ex_id}",
                headers=admin_headers,
                json={"is_active": False},
            )
            assert locked.status_code == 200
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1008


def test_pipeline_create_populates_session_workstations_and_policy_audits(tmp_path) -> None:
    app = _make_enforced_app(tmp_path)
    app.state.container.register_agent(
        RegisterAgentInput("PC-P1", "HOST-P1", "10.0.2.1", "2.0.0")
    )
    with TestClient(app) as client:
        admin_token = _login(client, "rootadmin", "AdminPass123!")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        created_session = client.post(
            "/api/v1/sessions",
            headers=admin_headers,
            json={
                "exam_name": "Pipeline Populated",
                "room_id": "B202",
                "gateway_id": "GW-B202",
                "workstation_ids": ["PC-P1"],
            },
        )
        assert created_session.status_code == 201
        session_id = created_session.json()["id"]
        with app.state.container.database.unit_of_work() as uow:
            assignments = uow.session_workstations.list_for_session(session_id)
            assert [item.agent_id for item in assignments] == ["PC-P1"]

        profile = client.post(
            "/api/v1/policy-profiles",
            headers=admin_headers,
            json={
                "id": "AUDIT_PROFILE",
                "label": "Audit Profile",
                "description": "Audited",
                "rules": {
                    "applications": {"allow": ["exam.exe"], "deny": []},
                    "network": {"block": []},
                    "devices": {"usb": "deny"},
                },
            },
        )
        assert profile.status_code == 201
        updated = client.put(
            "/api/v1/policy-profiles/AUDIT_PROFILE",
            headers=admin_headers,
            json={
                "label": "Audit Profile v2",
                "description": "Audited v2",
                "rules": {
                    "applications": {"allow": ["exam.exe"], "deny": ["chatgpt.exe"]},
                    "network": {"block": []},
                    "devices": {"usb": "deny"},
                },
            },
        )
        assert updated.status_code == 200
        deleted = client.delete(
            "/api/v1/policy-profiles/AUDIT_PROFILE", headers=admin_headers
        )
        assert deleted.status_code == 204
        with app.state.container.database.unit_of_work() as uow:
            actions = [event.action for event in uow.audits.list_for_session(None)]
        assert "POLICY_PROFILE_CREATED" in actions
        assert "POLICY_PROFILE_UPDATED" in actions
        assert "POLICY_PROFILE_DELETED" in actions
