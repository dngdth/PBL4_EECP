from datetime import UTC, datetime
from pathlib import Path

from app.domain.value_objects.enums import Severity
from app.main import create_app
from fastapi.testclient import TestClient


def _setup_session(client: TestClient, agent_ids: list[str]) -> str:
    for aid in agent_ids:
        client.post(
            "/api/v1/agents/register",
            json={
                "id": aid,
                "hostname": f"HOST-{aid}",
                "ip_address": f"192.168.1.{10 + int(aid.replace('PC', ''))}",
                "agent_version": "1.0.0",
            },
        )
    created = client.post(
        "/api/v1/sessions",
        json={"name": "Exam", "room": "A101", "agent_ids": agent_ids},
    ).json()
    session_id = created["id"]
    for aid in agent_ids:
        commands = client.get(f"/api/v1/agents/{aid}/commands").json()
        cmd = commands[0]
        client.post(
            f"/api/v1/commands/{cmd['id']}/acknowledge",
            json={
                "success": True,
                "policy_hash": cmd["payload"]["policy_hash"],
                "actor": aid,
            },
        )
    client.patch(
        f"/api/v1/sessions/{session_id}/status",
        json={"status": "READY"},
    )
    client.patch(
        f"/api/v1/sessions/{session_id}/status",
        json={"status": "RUNNING"},
    )
    return session_id


def test_forbidden_process_detected_creates_incident_with_process_name_evidence(
    tmp_path: Path,
) -> None:
    """When session is RUNNING, FORBIDDEN_PROCESS_DETECTED at WARNING creates distinct incident."""
    with TestClient(create_app(tmp_path / "test_proc.db")) as client:
        session_id = _setup_session(client, ["PC01"])

        res = client.post(
            f"/api/v1/sessions/{session_id}/telemetry",
            json={
                "workstation_id": "PC01",
                "event_type": "FORBIDDEN_PROCESS_DETECTED",
                "severity": "WARNING",
                "category": "FORBIDDEN_PROCESS",
                "action": "DETECTED",
                "payload": {
                    "process_name": "chatgpt.exe",
                    "severity": "WARNING",
                    "category": "FORBIDDEN_PROCESS",
                    "action": "DETECTED",
                },
            },
        )
        assert res.status_code == 202
        incident_id = res.json()["incident_id"]
        assert incident_id is not None, "Incident must be created for FORBIDDEN_PROCESS_DETECTED"

        container = client.app.state.container
        with container.database.unit_of_work() as uow:
            incidents = uow.incidents.list_for_session(session_id)

        assert len(incidents) == 1
        inc = incidents[0]
        assert inc.workstation_id == "PC01"
        assert inc.category == "FORBIDDEN_PROCESS"
        assert inc.severity == Severity.WARNING
        assert inc.evidence.get("process_name") == "chatgpt.exe"


def test_forbidden_process_deduplication_per_workstation(tmp_path: Path) -> None:
    """Repeated events on same workstation do not spam incidents; distinct workstations get distinct incidents."""
    with TestClient(create_app(tmp_path / "test_dedup.db")) as client:
        session_id = _setup_session(client, ["PC01", "PC02"])

        # PC01 first event -> incident created
        res1 = client.post(
            f"/api/v1/sessions/{session_id}/telemetry",
            json={
                "workstation_id": "PC01",
                "event_type": "FORBIDDEN_PROCESS_DETECTED",
                "severity": "WARNING",
                "category": "FORBIDDEN_PROCESS",
                "action": "DETECTED",
                "payload": {"process_name": "chatgpt.exe"},
            },
        )
        assert res1.status_code == 202
        inc1_id = res1.json()["incident_id"]
        assert inc1_id is not None

        # PC01 second event with same or running process -> deduplicated, no new incident
        res2 = client.post(
            f"/api/v1/sessions/{session_id}/telemetry",
            json={
                "workstation_id": "PC01",
                "event_type": "FORBIDDEN_PROCESS_DETECTED",
                "severity": "WARNING",
                "category": "FORBIDDEN_PROCESS",
                "action": "DETECTED",
                "payload": {"process_name": "chatgpt.exe"},
            },
        )
        assert res2.status_code == 202
        assert res2.json()["incident_id"] is None

        # PC02 event -> distinct incident created for PC02
        res3 = client.post(
            f"/api/v1/sessions/{session_id}/telemetry",
            json={
                "workstation_id": "PC02",
                "event_type": "FORBIDDEN_PROCESS_DETECTED",
                "severity": "WARNING",
                "category": "FORBIDDEN_PROCESS",
                "action": "DETECTED",
                "payload": {"process_name": "anydesk.exe"},
            },
        )
        assert res3.status_code == 202
        inc3_id = res3.json()["incident_id"]
        assert inc3_id is not None
        assert inc3_id != inc1_id

        container = client.app.state.container
        with container.database.unit_of_work() as uow:
            incidents = uow.incidents.list_for_session(session_id)

        assert len(incidents) == 2
        by_ws = {inc.workstation_id: inc for inc in incidents}
        assert "PC01" in by_ws
        assert "PC02" in by_ws
        assert by_ws["PC01"].evidence.get("process_name") == "chatgpt.exe"
        assert by_ws["PC02"].evidence.get("process_name") == "anydesk.exe"


def test_session_details_includes_forbidden_process_in_violations(tmp_path: Path) -> None:
    """_build_details lists FORBIDDEN_PROCESS_DETECTED alongside POLICY_VIOLATION."""
    with TestClient(create_app(tmp_path / "test_details.db")) as client:
        session_id = _setup_session(client, ["PC01"])

        client.post(
            f"/api/v1/sessions/{session_id}/telemetry",
            json={
                "workstation_id": "PC01",
                "event_type": "FORBIDDEN_PROCESS_DETECTED",
                "severity": "WARNING",
                "category": "FORBIDDEN_PROCESS",
                "action": "DETECTED",
                "payload": {"process_name": "chatgpt.exe"},
            },
        )

        detail = client.get(f"/api/v1/sessions/{session_id}")
        assert detail.status_code == 200
        violations = detail.json()["violations"]
        assert len(violations) == 1
        assert violations[0]["workstation_id"] == "PC01"
        assert violations[0]["category"] == "FORBIDDEN_PROCESS"
        assert violations[0]["destination"] == "chatgpt.exe"
        assert violations[0]["action"] == "DETECTED"
        assert violations[0]["occurred_at"] is not None
