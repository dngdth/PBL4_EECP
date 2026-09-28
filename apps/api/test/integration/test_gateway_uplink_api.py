import json
import time
from datetime import UTC, datetime
from pathlib import Path

from app.application.dtos.session_management import (
    CreateExamSessionInput,
    UpdateExamSessionStatusInput,
)
from app.domain.value_objects.enums import SessionState
from app.main import create_app
from fastapi.testclient import TestClient

from contracts.v2 import (
    Ack,
    AckStatus,
    AgentHello,
    Command,
    Event,
    EventType,
    GatewayEnvelope,
    GatewayHealth,
    GatewayHello,
    GatewayMessageType,
    PresenceHealth,
)


def _envelope(
    message_type, source, payload, message_id="msg-1", correlation_id=None
) -> dict:
    return GatewayEnvelope(
        protocol_version=2,
        message_type=message_type,
        message_id=message_id,
        correlation_id=correlation_id or message_id,
        source_id=source,
        target_id="backend",
        payload=payload,
    ).model_dump(mode="json")


def test_gateway_uplink_registers_maps_routes_and_persists_ack(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EECP_GATEWAY_BOOTSTRAP_TOKEN", "test-token")
    app = create_app(tmp_path / "gateway-uplink.db")
    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v2/gateways/ws/GW-A",
            headers={"Authorization": "Bearer test-token"},
        ) as websocket:
            hello = GatewayHello(
                protocol_version=2,
                gateway_id="GW-A",
                room_id="LAB-A",
                gateway_version="1.0.0",
            )
            websocket.send_json(
                _envelope(
                    GatewayMessageType.GATEWAY_HELLO,
                    "GW-A",
                    hello.model_dump(mode="json"),
                )
            )
            agent = AgentHello(
                protocol_version=2,
                agent_id="AGT-001",
                hostname="HOST-1",
                ip_address="192.0.2.1",
                agent_version="1.1.0",
            )
            websocket.send_json(
                _envelope(
                    GatewayMessageType.AGENT_HELLO,
                    "AGT-001",
                    agent.model_dump(mode="json"),
                    "hello-agent",
                )
            )

            deadline = time.monotonic() + 2
            while client.get("/api/v2/gateways/agents/AGT-001").json() is None:
                assert time.monotonic() < deadline
                time.sleep(0.01)

            health = GatewayHealth(
                protocol_version=2,
                gateway_id="GW-A",
                room_id="LAB-A",
                status=PresenceHealth.ONLINE,
                last_seen=datetime.now(UTC),
                connected_agent_count=1,
                backend_uplink_status=PresenceHealth.ONLINE,
            )
            websocket.send_json(
                _envelope(
                    GatewayMessageType.GATEWAY_HEALTH,
                    "GW-A",
                    health.model_dump(mode="json"),
                    "health-1",
                )
            )

            app.state.container.create_exam_session(
                CreateExamSessionInput("Exam", "LAB-A", ["AGT-001"])
            )
            routed = GatewayEnvelope.model_validate_json(websocket.receive_text())
            command = Command.model_validate(routed.payload)
            assert routed.target_id == "AGT-001"
            assert command.target_id == "AGT-001"

            ack = Ack(
                protocol_version=2,
                ack_id=f"ack_{command.command_id}",
                command_id=command.command_id,
                status=AckStatus.SUCCEEDED,
                applied_hash=command.policy_hash,
                service_version="1.1.0",
                occurred_at=command.issued_at,
                correlation_id=command.correlation_id,
            )
            websocket.send_json(
                _envelope(
                    GatewayMessageType.ACK,
                    "AGT-001",
                    ack.model_dump(mode="json", exclude_none=True),
                    ack.ack_id,
                    command.correlation_id,
                )
            )
            deadline = time.monotonic() + 2
            while True:
                with app.state.container.database.unit_of_work() as uow:
                    persisted = uow.commands.get(command.command_id)
                if persisted.status.value == "ACKNOWLEDGED":
                    break
                assert time.monotonic() < deadline
                time.sleep(0.01)

            app.state.container.update_exam_session_status(
                UpdateExamSessionStatusInput(
                    command.session_id, SessionState.READY, "teacher"
                )
            )
            app.state.container.update_exam_session_status(
                UpdateExamSessionStatusInput(
                    command.session_id, SessionState.RUNNING, "teacher"
                )
            )
            event = Event(
                protocol_version=2,
                event_id="EVT-001",
                session_id=command.session_id,
                agent_id="AGT-001",
                event_type=EventType.POLICY_VIOLATION,
                occurred_at=command.issued_at,
                payload={
                    "severity": "WARNING",
                    "category": "PROHIBITED_WEBSITE",
                    "action": "BLOCKED",
                    "destination": "example.invalid",
                },
                correlation_id=command.correlation_id,
            )
            websocket.send_json(
                _envelope(
                    GatewayMessageType.EVENT,
                    "AGT-001",
                    event.model_dump(mode="json"),
                    event.event_id,
                    event.correlation_id,
                )
            )
            deadline = time.monotonic() + 2
            while True:
                with app.state.container.database.unit_of_work() as uow:
                    events = uow.telemetry.list_for_session(command.session_id)
                if events:
                    break
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert events[0].correlation_id == command.correlation_id

        gateways = client.get("/api/v2/gateways").json()
        assert gateways[0]["gateway_id"] == "GW-A"
        assert gateways[0]["status"] == "OFFLINE"
        assert gateways[0]["connected_agent_count"] == 1
        assert gateways[0]["backend_uplink_status"] == "OFFLINE"


def test_gateway_uplink_rejects_malformed_message_without_crashing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EECP_GATEWAY_BOOTSTRAP_TOKEN", "test-token")
    with TestClient(
        create_app(tmp_path / "malformed.db")
    ) as client, client.websocket_connect(
        "/api/v2/gateways/ws/GW-A",
        headers={"Authorization": "Bearer test-token"},
    ) as websocket:
        hello = GatewayHello(
            protocol_version=2,
            gateway_id="GW-A",
            room_id="LAB-A",
            gateway_version="1.0.0",
        )
        websocket.send_json(
            _envelope(
                GatewayMessageType.GATEWAY_HELLO,
                "GW-A",
                hello.model_dump(mode="json"),
            )
        )
        websocket.send_text(json.dumps({"protocol_version": 99}))
        response = GatewayEnvelope.model_validate_json(websocket.receive_text())
        assert response.message_type == GatewayMessageType.ERROR
