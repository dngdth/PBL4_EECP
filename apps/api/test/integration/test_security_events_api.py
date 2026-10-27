from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.application.security import Principal, Role
from app.infrastructure.security import TokenService
from app.main import create_app
from app.presentation.api.routers.gateways import _agent_session_state
from fastapi.testclient import TestClient

from contracts.v2 import AgentSessionStateStatus

AUTH_KEY = "security-event-api-test-key-at-least-32-bytes"


def _running_session(client: TestClient) -> str:
    assert client.post(
        "/api/v1/agents/register",
        json={
            "id": "PC01",
            "hostname": "LAB-PC",
            "ip_address": "192.0.2.10",
            "agent_version": "1.1.0",
        },
    ).status_code == 201
    created = client.post(
        "/api/v1/sessions",
        json={"name": "Security Test", "room": "LAB", "agent_ids": ["PC01"]},
    ).json()
    commands = client.get("/api/v1/agents/PC01/commands").json()
    command = commands[0]
    assert client.post(
        f"/api/v1/commands/{command['id']}/acknowledge",
        json={
            "success": True,
            "policy_hash": command["payload"]["policy_hash"],
            "actor": "PC01",
        },
    ).status_code == 200
    assert client.patch(
        f"/api/v1/sessions/{created['id']}/status", json={"status": "READY"}
    ).status_code == 200
    assert client.patch(
        f"/api/v1/sessions/{created['id']}/status", json={"status": "RUNNING"}
    ).status_code == 200
    return created["id"]


def _post_event(
    client: TestClient,
    session_id: str,
    *,
    event_id: str,
    event_type: str,
    severity: str,
    action: str,
    occurred_at: datetime,
    payload: dict | None = None,
):
    return client.post(
        f"/api/v1/sessions/{session_id}/telemetry",
        json={
            "workstation_id": "PC01",
            "event_type": event_type,
            "severity": severity,
            "category": event_type,
            "action": action,
            "event_id": event_id,
            "occurred_at": occurred_at.isoformat(),
            "payload": payload or {"source": "privileged-service"},
        },
    )


def test_security_event_api_filters_orders_and_paginates(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "events-api.db")) as client:
        session_id = _running_session(client)
        base = datetime.now(UTC) - timedelta(minutes=1)
        assert _post_event(
            client,
            session_id,
            event_id="evt-login",
            event_type="USER_SESSION_CHANGED",
            severity="INFO",
            action="LOGON",
            occurred_at=base,
            payload={"source": "windows-service", "username": "STUDENT01"},
        ).status_code == 202
        assert _post_event(
            client,
            session_id,
            event_id="evt-process",
            event_type="ENFORCEMENT_ACTION",
            severity="WARNING",
            action="BLOCKED",
            occurred_at=base + timedelta(seconds=1),
            payload={
                "source": "privileged-service",
                "process_name": "zalo.exe",
                "pid": 42,
                "enforcement_result": "TERMINATED",
            },
        ).status_code == 202
        assert _post_event(
            client,
            session_id,
            event_id="evt-recovery",
            event_type="ENFORCEMENT_TAMPER",
            severity="CRITICAL",
            action="RECOVERY_FAILED",
            occurred_at=base + timedelta(seconds=2),
            payload={"source": "privileged-service", "recovery_status": "RECOVERY_FAILED"},
        ).status_code == 202

        page = client.get(
            f"/api/v1/sessions/{session_id}/security-events?offset=0&limit=2"
        )
        warning = client.get(
            f"/api/v1/sessions/{session_id}/security-events"
            "?severity=WARNING&event_type=ENFORCEMENT_ACTION&agent_id=PC01"
        )
        time_filtered = client.get(
            f"/api/v1/sessions/{session_id}/security-events",
            params={"occurred_from": (base + timedelta(seconds=1)).isoformat()},
        )

        assert page.status_code == warning.status_code == time_filtered.status_code == 200
        assert page.json()["total"] == 3
        assert [item["event_id"] for item in page.json()["items"]] == [
            "evt-recovery",
            "evt-process",
        ]
        assert [item["event_id"] for item in warning.json()["items"]] == ["evt-process"]
        assert [item["event_id"] for item in time_filtered.json()["items"]] == [
            "evt-recovery",
            "evt-process",
        ]
        assert warning.json()["items"][0]["process_name"] == "zalo.exe"
        assert warning.json()["items"][0]["pid"] == 42


def test_windows_login_info_does_not_create_incident_and_duplicate_is_idempotent(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "login-event.db")) as client:
        session_id = _running_session(client)
        at = datetime.now(UTC)
        first = _post_event(
            client,
            session_id,
            event_id="evt-login",
            event_type="USER_SESSION_CHANGED",
            severity="INFO",
            action="LOGON",
            occurred_at=at,
        )
        duplicate = _post_event(
            client,
            session_id,
            event_id="evt-login",
            event_type="USER_SESSION_CHANGED",
            severity="INFO",
            action="LOGON",
            occurred_at=at,
        )
        events = client.get(f"/api/v1/sessions/{session_id}/security-events").json()
        summary = client.get(f"/api/v1/sessions/{session_id}/summary").json()

        assert first.status_code == duplicate.status_code == 202
        assert first.json()["incident_id"] is None
        assert duplicate.json()["event_id"] == first.json()["event_id"]
        assert events["total"] == 1
        assert summary["incidents"] == []


def test_security_events_reject_non_running_and_finished_sessions(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "event-state.db")) as client:
        session_id = _running_session(client)
        assert client.patch(
            f"/api/v1/sessions/{session_id}/status", json={"status": "FINISHED"}
        ).status_code == 200

        late = _post_event(
            client,
            session_id,
            event_id="evt-late",
            event_type="ENFORCEMENT_ACTION",
            severity="WARNING",
            action="BLOCKED",
            occurred_at=datetime.now(UTC) - timedelta(seconds=5),
        )

        assert late.status_code == 422


def test_late_privileged_event_is_accepted_without_reactivating_session(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "late-event.db")
    with TestClient(app) as client:
        session_id = _running_session(client)
        occurred_at = datetime.now(UTC)
        assert client.patch(
            f"/api/v1/sessions/{session_id}/status", json={"status": "FINISHED"}
        ).status_code == 200

        first = _post_event(
            client,
            session_id,
            event_id="evt-late-valid",
            event_type="ENFORCEMENT_ACTION",
            severity="WARNING",
            action="BLOCKED",
            occurred_at=occurred_at,
            payload={
                "source": "privileged-service",
                "process_name": "blocked.exe",
                "pid": 42,
            },
        )
        duplicate = _post_event(
            client,
            session_id,
            event_id="evt-late-valid",
            event_type="ENFORCEMENT_ACTION",
            severity="WARNING",
            action="BLOCKED",
            occurred_at=occurred_at,
            payload={
                "source": "privileged-service",
                "process_name": "blocked.exe",
                "pid": 42,
            },
        )

        assert first.status_code == duplicate.status_code == 202
        events = client.get(f"/api/v1/sessions/{session_id}/security-events").json()
        assert events["total"] == 1
        assert events["items"][0]["payload"]["late_arrival"] is True
        assert _agent_session_state(app.state.container, "PC01").status == (
            AgentSessionStateStatus.INACTIVE
        )


def test_late_event_rejects_untrusted_source_collision_and_expired_grace(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "late-event-reject.db")
    with TestClient(app) as client:
        session_id = _running_session(client)
        occurred_at = datetime.now(UTC)
        assert client.patch(
            f"/api/v1/sessions/{session_id}/status", json={"status": "FINISHED"}
        ).status_code == 200
        session = app.state.container.pipeline_service.get_session(session_id)

        untrusted = _post_event(
            client,
            session_id,
            event_id="evt-untrusted",
            event_type="ENFORCEMENT_ACTION",
            severity="WARNING",
            action="BLOCKED",
            occurred_at=occurred_at,
            payload={"source": "agent-user-process"},
        )
        accepted = _post_event(
            client,
            session_id,
            event_id="evt-collision",
            event_type="ENFORCEMENT_ACTION",
            severity="WARNING",
            action="BLOCKED",
            occurred_at=occurred_at,
            payload={"source": "privileged-service", "pid": 42},
        )
        collision = _post_event(
            client,
            session_id,
            event_id="evt-collision",
            event_type="ENFORCEMENT_ACTION",
            severity="WARNING",
            action="BLOCKED",
            occurred_at=occurred_at,
            payload={"source": "privileged-service", "pid": 99},
        )
        app.state.container.pipeline_service._clock = lambda: (
            session.finished_at + timedelta(hours=25)
        )
        expired = _post_event(
            client,
            session_id,
            event_id="evt-expired",
            event_type="ENFORCEMENT_ACTION",
            severity="WARNING",
            action="BLOCKED",
            occurred_at=occurred_at,
            payload={"source": "privileged-service"},
        )

        assert untrusted.status_code == 422
        assert accepted.status_code == 202
        assert collision.status_code == 422
        assert expired.status_code == 422


def test_security_event_api_requires_auth_and_session_scope(tmp_path: Path) -> None:
    app = create_app(tmp_path / "security-rbac.db")
    with TestClient(app) as client:
        session_id = _running_session(client)
        app.state.settings = replace(app.state.settings, environment="production-like")
        app.state.token_service = TokenService(AUTH_KEY)
        token = app.state.token_service.issue(
            Principal("examiner", Role.EXAMINER, frozenset({"OTHER-SESSION"}))
        )

        unauthenticated = client.get(
            f"/api/v1/sessions/{session_id}/security-events"
        )
        existing_wrong_scope = client.get(
            f"/api/v1/sessions/{session_id}/security-events",
            headers={"Authorization": f"Bearer {token}"},
        )
        nonexistent_wrong_scope = client.get(
            "/api/v1/sessions/DOES-NOT-EXIST/security-events",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert unauthenticated.status_code == 401
    assert existing_wrong_scope.status_code == 403
    assert nonexistent_wrong_scope.status_code == 403
    with app.state.container.database.unit_of_work() as uow:
        denials = [
            event
            for event in uow.audits.list_for_session(None)
            if event.action == "AUTHORIZATION_DENIED"
        ]
        assert uow.audits.verify_chain(None)
    assert {event.target for event in denials} >= {session_id, "DOES-NOT-EXIST"}
    assert all(event.session_id is None for event in denials)
    assert all(event.details["resource_type"] == "SESSION" for event in denials)
