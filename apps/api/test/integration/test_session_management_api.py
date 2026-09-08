from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.main import create_app
from fastapi.testclient import TestClient


def _register_agent(
    client: TestClient,
    agent_id: str,
    hostname: str,
    ip_address: str,
) -> None:
    response = client.post(
        "/api/v1/agents/register",
        json={
            "id": agent_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "agent_version": "1.0.0",
        },
    )
    assert response.status_code == 201


def _create_management_session(client: TestClient, agent_id: str = "PC01") -> dict:
    response = client.post(
        "/api/v1/sessions",
        json={"name": "Exam", "room": "A101", "agent_ids": [agent_id]},
    )
    assert response.status_code == 201
    return response.json()


def _apply_policy(client: TestClient, agent_id: str = "PC01") -> None:
    commands = client.get(f"/api/v1/agents/{agent_id}/commands")
    assert commands.status_code == 200
    command = commands.json()[0]
    response = client.post(
        f"/api/v1/commands/{command['id']}/acknowledge",
        json={
            "success": True,
            "policy_hash": command["payload"]["policy_hash"],
            "actor": agent_id,
        },
    )
    assert response.status_code == 200


def _make_agent_stale(client: TestClient, agent_id: str) -> None:
    database = client.app.state.container.database
    with database.unit_of_work() as uow:
        agent = uow.agents.get(agent_id)
        agent.last_seen = datetime.now(UTC) - timedelta(minutes=1)
        uow.agents.save(agent)
        uow.commit()


def test_create_list_and_get_management_session(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "api.db")) as client:
        _register_agent(client, "PC01", "DESKTOP-ALPHA", "192.168.3.51")
        _register_agent(client, "PC02", "DESKTOP-BRAVO", "192.168.3.52")

        created = client.post(
            "/api/v1/sessions",
            json={
                "name": "PBL4 Final",
                "room": "A101",
                "agent_ids": ["PC01", "PC02"],
            },
        )

        assert created.status_code == 201
        body = created.json()
        assert body["name"] == "PBL4 Final"
        assert body["room"] == "A101"
        assert body["status"] == "CREATED"
        assert body["agent_count"] == 2

        listed = client.get("/api/v1/sessions")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [body["id"]]

        detail = client.get(f"/api/v1/sessions/{body['id']}")
        assert detail.status_code == 200
        assert [item["id"] for item in detail.json()["agents"]] == ["PC01", "PC02"]
        assert [item["hostname"] for item in detail.json()["agents"]] == [
            "DESKTOP-ALPHA",
            "DESKTOP-BRAVO",
        ]
        assert [item["ip_address"] for item in detail.json()["agents"]] == [
            "192.168.3.51",
            "192.168.3.52",
        ]


def test_update_management_session_from_created_to_ready(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "ready.db")) as client:
        _register_agent(client, "PC01", "DESKTOP-ALPHA", "192.168.3.51")
        created = _create_management_session(client)
        _apply_policy(client)

        response = client.patch(
            f"/api/v1/sessions/{created['id']}/status",
            json={"status": "READY"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "READY"


def test_running_session_lists_agent_policy_violation(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "violation.db")) as client:
        _register_agent(client, "PC01", "DESKTOP-ALPHA", "192.168.3.51")
        created = _create_management_session(client)
        _apply_policy(client)
        ready = client.patch(
            f"/api/v1/sessions/{created['id']}/status",
            json={"status": "READY"},
        )
        assert ready.status_code == 200
        running = client.patch(
            f"/api/v1/sessions/{created['id']}/status",
            json={"status": "RUNNING"},
        )
        assert running.status_code == 200

        telemetry = client.post(
            f"/api/v1/sessions/{created['id']}/telemetry",
            json={
                "workstation_id": "PC01",
                "event_type": "POLICY_VIOLATION",
                "severity": "WARNING",
                "category": "PROHIBITED_WEBSITE",
                "action": "BLOCKED",
                "destination": "chatgpt.com",
            },
        )

        assert telemetry.status_code == 202
        assert telemetry.json()["incident_id"] is not None
        detail = client.get(f"/api/v1/sessions/{created['id']}")
        assert detail.status_code == 200
        assert detail.json()["violations"][0]["workstation_id"] == "PC01"
        assert detail.json()["violations"][0]["destination"] == "chatgpt.com"


def test_ready_rejects_pending_policy(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "pending-policy.db")) as client:
        _register_agent(client, "PC01", "DESKTOP-ALPHA", "192.168.3.51")
        created = _create_management_session(client)

        response = client.patch(
            f"/api/v1/sessions/{created['id']}/status",
            json={"status": "READY"},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "READINESS_GATE"
        assert "PC01 (PENDING)" in response.json()["detail"]


def test_create_rejects_agent_in_active_session(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "active-conflict.db")) as client:
        _register_agent(client, "PC01", "DESKTOP-ALPHA", "192.168.3.51")
        first = _create_management_session(client)

        response = client.post(
            "/api/v1/sessions",
            json={"name": "Second", "room": "A102", "agent_ids": ["PC01"]},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "SESSION_CONFLICT"
        assert first["id"] in response.json()["detail"]


def test_update_management_session_rejects_skipped_state(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "invalid-state.db")) as client:
        _register_agent(client, "PC01", "DESKTOP-ALPHA", "192.168.3.51")
        created = _create_management_session(client)

        response = client.patch(
            f"/api/v1/sessions/{created['id']}/status",
            json={"status": "RUNNING"},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "INVALID_STATE"


def test_ready_rejects_agent_that_became_stale(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "stale.db")) as client:
        _register_agent(client, "PC01", "DESKTOP-ALPHA", "192.168.3.51")
        created = _create_management_session(client)
        _make_agent_stale(client, "PC01")

        response = client.patch(
            f"/api/v1/sessions/{created['id']}/status",
            json={"status": "READY"},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "READINESS_GATE"
        assert "PC01" in response.json()["detail"]


def test_create_management_session_rejects_unknown_agent(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "unknown-agent.db")) as client:
        response = client.post(
            "/api/v1/sessions",
            json={"name": "Exam", "room": "A101", "agent_ids": ["PC99"]},
        )

        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"


def test_create_management_session_rejects_offline_agent(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "offline-agent.db")) as client:
        _register_agent(client, "PC01", "DESKTOP-ALPHA", "192.168.3.51")
        _make_agent_stale(client, "PC01")

        response = client.post(
            "/api/v1/sessions",
            json={"name": "Exam", "room": "A101", "agent_ids": ["PC01"]},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "READINESS_GATE"


def test_create_session_rejects_mixed_management_and_pipeline_fields(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "mixed-fields.db")) as client:
        response = client.post(
            "/api/v1/sessions",
            json={
                "name": "Exam",
                "exam_name": "Legacy Exam",
                "room": "A101",
                "agent_ids": ["PC01"],
            },
        )

        assert response.status_code == 422


def test_create_management_session_rejects_blank_agent_item(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "blank-agent.db")) as client:
        _register_agent(client, "PC01", "DESKTOP-ALPHA", "192.168.3.51")

        response = client.post(
            "/api/v1/sessions",
            json={"name": "Exam", "room": "A101", "agent_ids": ["PC01", " "]},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "DOMAIN_VALIDATION"


def test_create_management_session_validates_duplicate_unknown_agents_first(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "duplicate-unknown.db")) as client:
        response = client.post(
            "/api/v1/sessions",
            json={"name": "Exam", "room": "A101", "agent_ids": ["PC99", " PC99 "]},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "DOMAIN_VALIDATION"


def test_create_management_session_validates_duplicate_offline_agents_without_refresh(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "duplicate-offline.db")) as client:
        _register_agent(client, "PC01", "DESKTOP-ALPHA", "192.168.3.51")
        _make_agent_stale(client, "PC01")

        response = client.post(
            "/api/v1/sessions",
            json={"name": "Exam", "room": "A101", "agent_ids": ["PC01", "PC01"]},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "DOMAIN_VALIDATION"
        database = client.app.state.container.database
        with database.unit_of_work() as uow:
            assert uow.agents.get("PC01").status.value == "ONLINE"
            assert uow.sessions.list_all() == []


@pytest.mark.parametrize(
    ("name", "room"),
    [(" ", "A101"), ("Exam", " ")],
)
def test_create_management_session_validates_name_and_room_before_unknown_agent(
    tmp_path: Path,
    name: str,
    room: str,
) -> None:
    with TestClient(create_app(tmp_path / f"blank-{name!r}-{room!r}.db")) as client:
        response = client.post(
            "/api/v1/sessions",
            json={"name": name, "room": room, "agent_ids": ["PC99"]},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "DOMAIN_VALIDATION"
