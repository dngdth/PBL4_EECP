from datetime import UTC, datetime
from pathlib import Path

from app.application.dtos.exam_pipeline import TelemetryInput
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


def test_test1_pc01_repeated_events_do_not_duplicate_incident(tmp_path: Path) -> None:
    """TEST 1: PC01 gửi 3 event giống nhau -> không tạo 3 Incident trùng."""
    with TestClient(create_app(tmp_path / "test1.db")) as client:
        session_id = _setup_session(client, ["PC01"])
        incidents_created = []

        for _ in range(3):
            res = client.post(
                f"/api/v1/sessions/{session_id}/telemetry",
                json={
                    "workstation_id": "PC01",
                    "event_type": "POLICY_VIOLATION",
                    "severity": "WARNING",
                    "category": "PROHIBITED_WEBSITE",
                    "action": "BLOCKED",
                    "destination": "gambling.invalid",
                },
            )
            assert res.status_code == 202
            inc_id = res.json()["incident_id"]
            if inc_id:
                incidents_created.append(inc_id)

        assert len(incidents_created) == 1

        container = client.app.state.container
        with container.database.unit_of_work() as uow:
            telemetry = uow.telemetry.list_for_session(session_id)
            incidents = uow.incidents.list_for_session(session_id)

        assert len(telemetry) == 3
        assert len(incidents) == 1
        assert incidents[0].workstation_id == "PC01"
        assert incidents[0].category == "POLICY_VIOLATION"


def test_test2_pc01_and_pc02_violations_create_distinct_incidents(tmp_path: Path) -> None:
    """TEST 2: PC01 và PC02 cùng vi phạm -> phân biệt vi phạm từng workstation."""
    with TestClient(create_app(tmp_path / "test2.db")) as client:
        session_id = _setup_session(client, ["PC01", "PC02"])

        res1 = client.post(
            f"/api/v1/sessions/{session_id}/telemetry",
            json={
                "workstation_id": "PC01",
                "event_type": "POLICY_VIOLATION",
                "severity": "WARNING",
                "category": "PROHIBITED_WEBSITE",
                "action": "BLOCKED",
                "destination": "gambling.invalid",
            },
        )
        assert res1.status_code == 202
        inc1_id = res1.json()["incident_id"]
        assert inc1_id is not None

        res2 = client.post(
            f"/api/v1/sessions/{session_id}/telemetry",
            json={
                "workstation_id": "PC02",
                "event_type": "POLICY_VIOLATION",
                "severity": "WARNING",
                "category": "PROHIBITED_WEBSITE",
                "action": "BLOCKED",
                "destination": "cheating.invalid",
            },
        )
        assert res2.status_code == 202
        inc2_id = res2.json()["incident_id"]
        assert inc2_id is not None, "PC02 violation must NOT be swallowed or merged"
        assert inc1_id != inc2_id

        container = client.app.state.container
        with container.database.unit_of_work() as uow:
            incidents = uow.incidents.list_for_session(session_id)

        assert len(incidents) == 2
        by_ws = {inc.workstation_id: inc for inc in incidents}
        assert "PC01" in by_ws
        assert "PC02" in by_ws
        assert by_ws["PC01"].category == "POLICY_VIOLATION"
        assert by_ws["PC02"].category == "POLICY_VIOLATION"


def test_test3_resend_same_event_id_deduplicates(tmp_path: Path) -> None:
    """TEST 3: Gửi lại cùng event_id -> không tạo Telemetry Event hay Incident trùng."""
    with TestClient(create_app(tmp_path / "test3.db")) as client:
        session_id = _setup_session(client, ["PC01"])
        container = client.app.state.container

        telemetry_in = TelemetryInput(
            session_id=session_id,
            workstation_id="PC01",
            event_type="POLICY_VIOLATION",
            severity=Severity.WARNING,
            category="PROHIBITED_WEBSITE",
            action="BLOCKED",
            destination="example.invalid",
            event_id="EVT-UNIQUE-123",
        )
        evt1, inc1, dup1 = container.pipeline_service.ingest_protocol_event(telemetry_in)
        assert dup1 is False
        assert inc1 is not None

        # Re-send same event_id directly via pipeline service
        evt2, inc2, dup2 = container.pipeline_service.ingest_protocol_event(telemetry_in)
        assert dup2 is True
        assert inc2 is None
        assert evt2.id == evt1.id

        with container.database.unit_of_work() as uow:
            telemetry = uow.telemetry.list_for_session(session_id)
            incidents = uow.incidents.list_for_session(session_id)

        assert len(telemetry) == 1
        assert len(incidents) == 1


def test_test4_gateway_buffer_flush_consistency(tmp_path: Path) -> None:
    """TEST 4: Gateway buffer flush -> dữ liệu Incident và Event nhất quán."""
    import asyncio

    from app.application.dtos.gateways import BindAgentToGatewayInput, RegisterGatewayInput
    from app.presentation.api.routers.gateways import _handle_gateway_message

    from apps.gateway.app.event_buffer import EventBuffer
    from contracts.v2 import Event, EventType, GatewayEnvelope, GatewayMessageType

    with TestClient(create_app(tmp_path / "test4.db")) as client:
        container = client.app.state.container
        session_id = _setup_session(client, ["PC01", "PC02"])

        # Register gateway and bindings
        container.register_gateway(RegisterGatewayInput("GW-TEST", "A101", "1.0"))
        container.bind_agent_to_gateway(BindAgentToGatewayInput("PC01", "GW-TEST"))
        container.bind_agent_to_gateway(BindAgentToGatewayInput("PC02", "GW-TEST"))

        # Setup local gateway event buffer
        buffer = EventBuffer(tmp_path / "gw-buffer.db", max_rows=1000, degraded_threshold=800)
        # PC01 generates 2 duplicate events while offline
        for i in range(2):
            evt_pc01 = Event(
                protocol_version=2,
                event_id=f"EVT-BUF-PC01-{i}",
                session_id=session_id,
                agent_id="PC01",
                event_type=EventType.POLICY_VIOLATION,
                occurred_at=datetime.now(UTC),
                correlation_id=f"corr-01-{i}",
                payload={
                    "severity": "WARNING",
                    "category": "PROHIBITED_WEBSITE",
                    "action": "BLOCKED",
                    "destination": "gambling.invalid",
                },
            )
            buffer.put(
                GatewayEnvelope(
                    protocol_version=2,
                    message_type=GatewayMessageType.EVENT,
                    message_id=evt_pc01.event_id,
                    correlation_id=evt_pc01.correlation_id,
                    source_id="PC01",
                    target_id="backend",
                    payload=evt_pc01.model_dump(mode="json"),
                )
            )

        # PC02 generates 1 event while offline
        evt_pc02 = Event(
            protocol_version=2,
            event_id="EVT-BUF-PC02-0",
            session_id=session_id,
            agent_id="PC02",
            event_type=EventType.POLICY_VIOLATION,
            occurred_at=datetime.now(UTC),
            correlation_id="corr-02-0",
            payload={
                "severity": "WARNING",
                "category": "PROHIBITED_WEBSITE",
                "action": "BLOCKED",
                "destination": "cheating.invalid",
            },
        )
        buffer.put(
            GatewayEnvelope(
                protocol_version=2,
                message_type=GatewayMessageType.EVENT,
                message_id=evt_pc02.event_id,
                correlation_id=evt_pc02.correlation_id,
                source_id="PC02",
                target_id="backend",
                payload=evt_pc02.model_dump(mode="json"),
            )
        )

        assert buffer.count() == 3

        # Flush buffer to backend
        while buffer.count() > 0:
            batch = buffer.due(datetime.now(UTC), limit=10)
            for item in batch:
                asyncio.run(_handle_gateway_message("GW-TEST", item.envelope, container))
                buffer.complete(item.event_id, datetime.now(UTC))

        assert buffer.count() == 0

        # Verify backend consistency
        with container.database.unit_of_work() as uow:
            telemetry = uow.telemetry.list_for_session(session_id)
            incidents = uow.incidents.list_for_session(session_id)

        # 3 telemetry events preserved
        assert len(telemetry) == 3
        # 2 distinct incidents (1 for PC01 deduplicated, 1 for PC02 distinct)
        assert len(incidents) == 2
        by_ws = {inc.workstation_id: inc for inc in incidents}
        assert "PC01" in by_ws
        assert "PC02" in by_ws
