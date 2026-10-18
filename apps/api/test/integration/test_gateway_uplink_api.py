import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from app.application.dtos.session_management import (
    CreateExamSessionInput,
    UpdateExamSessionStatusInput,
)
from app.application.security import Principal, Role
from app.domain.value_objects.enums import SessionState
from app.infrastructure.security import TokenService
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


def test_gateway_uplink_rejects_ack_from_wrong_agent(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EECP_GATEWAY_BOOTSTRAP_TOKEN", "test-token")
    app = create_app(tmp_path / "gateway-ack-mismatch.db")
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
            for agent_id in ("AGT-001", "AGT-002"):
                agent = AgentHello(
                    protocol_version=2,
                    agent_id=agent_id,
                    hostname=f"HOST-{agent_id}",
                    ip_address="192.0.2.1",
                    agent_version="1.1.0",
                )
                websocket.send_json(
                    _envelope(
                        GatewayMessageType.AGENT_HELLO,
                        agent_id,
                        agent.model_dump(mode="json"),
                        f"hello-{agent_id}",
                    )
                )

            deadline = time.monotonic() + 2
            while client.get("/api/v2/gateways/agents/AGT-002").json() is None:
                assert time.monotonic() < deadline
                time.sleep(0.01)

            app.state.container.create_exam_session(
                CreateExamSessionInput("Exam", "LAB-A", ["AGT-001"])
            )
            routed = GatewayEnvelope.model_validate_json(websocket.receive_text())
            command = Command.model_validate(routed.payload)
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
                    "AGT-002",
                    ack.model_dump(mode="json", exclude_none=True),
                    ack.ack_id,
                    command.correlation_id,
                )
            )

            err_msg = GatewayEnvelope.model_validate_json(websocket.receive_text())
            assert err_msg.message_type == GatewayMessageType.ERROR

            with app.state.container.database.unit_of_work() as uow:
                persisted = uow.commands.get(command.command_id)
            assert persisted.status.value == "DELIVERED"


def test_production_like_disables_http_ack_while_gateway_ack_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EECP_GATEWAY_BOOTSTRAP_TOKEN", "prod-gw-token")
    app = create_app(tmp_path / "prod-gw-ack.db")
    app.state.settings = replace(app.state.settings, environment="production-like")
    app.state.token_service = TokenService(
        "phase-9-http-auth-key-that-is-at-least-thirty-two-bytes"
    )

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v2/gateways/ws/GW-PROD",
            headers={"Authorization": "Bearer prod-gw-token"},
        ) as websocket:
            hello = GatewayHello(
                protocol_version=2,
                gateway_id="GW-PROD",
                room_id="ROOM-PROD",
                gateway_version="1.0.0",
            )
            websocket.send_json(
                _envelope(
                    GatewayMessageType.GATEWAY_HELLO,
                    "GW-PROD",
                    hello.model_dump(mode="json"),
                )
            )
            agent = AgentHello(
                protocol_version=2,
                agent_id="AGT-PROD",
                hostname="HOST-AGT-PROD",
                ip_address="192.0.2.1",
                agent_version="1.1.0",
            )
            websocket.send_json(
                _envelope(
                    GatewayMessageType.AGENT_HELLO,
                    "AGT-PROD",
                    agent.model_dump(mode="json"),
                    "hello-agt-prod",
                )
            )

            deadline = time.monotonic() + 2
            while client.get("/api/v2/gateways/agents/AGT-PROD").json() is None:
                assert time.monotonic() < deadline
                time.sleep(0.01)

            created = app.state.container.create_exam_session(
                CreateExamSessionInput("Exam", "ROOM-PROD", ["AGT-PROD"])
            )
            routed = GatewayEnvelope.model_validate_json(websocket.receive_text())
            command = Command.model_validate(routed.payload)
            assert command.target_id == "AGT-PROD"

            # 1. Direct HTTP ACK is disabled in production-like for any role
            admin_token = app.state.token_service.issue(
                Principal("admin-user", Role.ADMIN, frozenset())
            )
            http_ack = client.post(
                f"/api/v1/commands/{command.command_id}/acknowledge",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "success": True,
                    "policy_hash": command.policy_hash,
                    "actor": "AGT-PROD",
                },
            )
            assert http_ack.status_code == 403
            assert "direct HTTP acknowledgement is disabled" in http_ack.json()["detail"]

            # Command remains DELIVERED
            with app.state.container.database.unit_of_work() as uow:
                persisted = uow.commands.get(command.command_id)
            assert persisted.status.value == "DELIVERED"

            # 2. Gateway WebSocket ACK succeeds in production-like
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
                    "AGT-PROD",
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

            assert persisted.status.value == "ACKNOWLEDGED"
            with app.state.container.database.unit_of_work() as uow:
                session = uow.sessions.get(created.session.id)
            assert session.workstations["AGT-PROD"].actual_policy_hash == command.policy_hash
            assert session.workstations["AGT-PROD"].policy_compliant is True


def test_gateway_ack_rejected_when_command_pending_and_succeeds_when_delivered(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EECP_GATEWAY_BOOTSTRAP_TOKEN", "test-token")
    app = create_app(tmp_path / "gateway-pending-ack.db")
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

            session = app.state.container.create_exam_session(
                CreateExamSessionInput("Exam Pending", "LAB-A", ["AGT-001"])
            ).session
            routed = GatewayEnvelope.model_validate_json(websocket.receive_text())
            cmd_dispatched = Command.model_validate(routed.payload)
            assert cmd_dispatched.target_id == "AGT-001"

            with app.state.container.database.unit_of_work() as uow:
                first_cmd = uow.commands.get(cmd_dispatched.command_id)

            # Create a second command directly in DB in PENDING status for AGT-001
            from app.domain.entities.operations import Command as DomainCommand
            from app.domain.value_objects.enums import CommandType as DomainCommandType
            from app.domain.value_objects.primitives import new_id

            pending_cmd_id = new_id("cmd")
            pending_cmd = DomainCommand(
                id=pending_cmd_id,
                session_id=session.id,
                target_id="AGT-001",
                type=DomainCommandType.APPLY_POLICY,
                payload=dict(first_cmd.payload),
            )
            with app.state.container.database.unit_of_work() as uow:
                uow.commands.add_many([pending_cmd])
                uow.commit()

            with app.state.container.database.unit_of_work() as uow:
                assert uow.commands.get(pending_cmd_id).status.value == "PENDING"

            # 1. Gateway sends ACK while command is still PENDING -> MUST be rejected
            ack_pending = Ack(
                protocol_version=2,
                ack_id=f"ack_{pending_cmd_id}",
                command_id=pending_cmd_id,
                status=AckStatus.SUCCEEDED,
                applied_hash=first_cmd.payload["policy_hash"],
                service_version="1.1.0",
                occurred_at=datetime.now(UTC),
                correlation_id="corr-pending",
            )
            websocket.send_json(
                _envelope(
                    GatewayMessageType.ACK,
                    "AGT-001",
                    ack_pending.model_dump(mode="json", exclude_none=True),
                    ack_pending.ack_id,
                    "corr-pending",
                )
            )

            err_msg = GatewayEnvelope.model_validate_json(websocket.receive_text())
            assert err_msg.message_type == GatewayMessageType.ERROR
            assert "invalid Protocol v2 message" in str(err_msg.payload)

            # The command was rejected for ACK, then routed/delivered to the Gateway
            routed_envelope = GatewayEnvelope.model_validate_json(websocket.receive_text())
            assert routed_envelope.message_type == GatewayMessageType.COMMAND
            assert routed_envelope.target_id == "AGT-001"

            with app.state.container.database.unit_of_work() as uow:
                persisted = uow.commands.get(pending_cmd_id)
                assert persisted.status.value == "DELIVERED"
                assert persisted.acknowledged_at is None

            # 2. Gateway sends ACK for DELIVERED command -> MUST succeed
            ack_delivered = Ack(
                protocol_version=2,
                ack_id=f"ack_{pending_cmd_id}_2",
                command_id=pending_cmd_id,
                status=AckStatus.SUCCEEDED,
                applied_hash=first_cmd.payload["policy_hash"],
                service_version="1.1.0",
                occurred_at=datetime.now(UTC),
                correlation_id="corr-delivered",
            )
            websocket.send_json(
                _envelope(
                    GatewayMessageType.ACK,
                    "AGT-001",
                    ack_delivered.model_dump(mode="json", exclude_none=True),
                    ack_delivered.ack_id,
                    "corr-delivered",
                )
            )

            deadline = time.monotonic() + 2
            while True:
                with app.state.container.database.unit_of_work() as uow:
                    persisted = uow.commands.get(pending_cmd_id)
                if persisted.status.value == "ACKNOWLEDGED":
                    break
                assert time.monotonic() < deadline
                time.sleep(0.01)

            assert persisted.status.value == "ACKNOWLEDGED"
            assert persisted.acknowledged_at is not None
