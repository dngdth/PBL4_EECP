import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.gateways import BindAgentToGatewayInput, RegisterGatewayInput
from app.application.use_cases.agents.management import RegisterAgent
from app.application.use_cases.gateways.management import (
    BindAgentToGateway,
    ListAgentsForGateway,
    RegisterGateway,
    ResolveGatewayForAgent,
    UpdateGatewayHealth,
)
from app.domain.entities.gateway import GatewayStatus
from app.infrastructure.persistence.database import SqliteDatabase

from apps.gateway.app.backend_client import BackendUplink
from apps.gateway.app.config import GatewaySettings
from apps.gateway.app.connections import AgentConnectionManager
from apps.gateway.app.presence import PresenceStore
from apps.gateway.app.routing import GatewayRouter
from contracts.v2 import (
    Ack,
    AckStatus,
    Command,
    CommandType,
    Event,
    EventType,
    GatewayEnvelope,
    GatewayMessageType,
    HealthCheckPayload,
    Presence,
    PresenceHealth,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def test_gateway_repository_health_binding_and_rebind(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "gateway.db")
    database.initialize()
    register_gateway = RegisterGateway(database.unit_of_work, lambda: NOW)
    update_health = UpdateGatewayHealth(database.unit_of_work, lambda: NOW)
    bind = BindAgentToGateway(database.unit_of_work, lambda: NOW)
    resolve = ResolveGatewayForAgent(database.unit_of_work)

    register_gateway(RegisterGatewayInput("GW-A", "LAB-A", "1.0.0"))
    register_gateway(RegisterGatewayInput("GW-B", "LAB-B", "1.0.0"))
    RegisterAgent(database.unit_of_work, lambda: NOW)(
        RegisterAgentInput("AGT-001", "HOST", "192.0.2.1", "1.1.0")
    )
    bind(BindAgentToGatewayInput("AGT-001", "GW-A"))
    assert resolve("AGT-001").gateway_id == "GW-A"

    bind(BindAgentToGatewayInput("AGT-001", "GW-B"))
    assert resolve("AGT-001").gateway_id == "GW-B"
    assert ListAgentsForGateway(database.unit_of_work)("GW-A") == []

    offline = update_health("GW-B", online=False)
    assert offline.status == GatewayStatus.OFFLINE
    assert update_health("GW-B").status == GatewayStatus.ONLINE
    health = update_health(
        "GW-B",
        connected_agent_count=3,
        backend_uplink_status=GatewayStatus.DEGRADED,
    )
    assert health.connected_agent_count == 3
    assert health.backend_uplink_status == GatewayStatus.DEGRADED


class FakeSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.closed: list[tuple[int, str | None]] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed.append((code, reason))


class FakeBackend:
    def __init__(self):
        self.messages: list[GatewayEnvelope] = []

    async def send(self, envelope: GatewayEnvelope) -> None:
        self.messages.append(envelope)


def _command_envelope(target: str = "AGT-002") -> GatewayEnvelope:
    command = Command(
        protocol_version=2,
        command_id="CMD-001",
        command_type=CommandType.HEALTH_CHECK,
        target_id=target,
        session_id="SES-001",
        issued_at=NOW,
        deadline=NOW + timedelta(minutes=1),
        payload=HealthCheckPayload(nonce="health"),
        correlation_id="CORR-001",
    )
    return GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.COMMAND,
        message_id="MSG-001",
        correlation_id="CORR-001",
        source_id="backend",
        target_id=target,
        payload=command.model_dump(mode="json"),
    )


def test_multiple_agents_route_only_to_target_and_report_offline() -> None:
    async def scenario() -> None:
        connections = AgentConnectionManager()
        backend = FakeBackend()
        router = GatewayRouter("GW-A", connections, PresenceStore(), backend)
        sockets = {agent: FakeSocket() for agent in ("AGT-001", "AGT-002", "AGT-003")}
        for agent, socket in sockets.items():
            await connections.connect(agent, socket)

        await router.route_backend(_command_envelope())
        assert sockets["AGT-001"].sent == []
        assert len(sockets["AGT-002"].sent) == 1
        assert sockets["AGT-003"].sent == []

        await connections.disconnect("AGT-002", sockets["AGT-002"])
        await router.route_backend(_command_envelope())
        failure = backend.messages[-1]
        assert failure.message_type == GatewayMessageType.DELIVERY_FAILURE
        assert failure.correlation_id == "CORR-001"

    asyncio.run(scenario())


def test_duplicate_agent_connection_newest_wins_and_stale_disconnect_is_ignored() -> None:
    async def scenario() -> None:
        manager = AgentConnectionManager()
        old = FakeSocket()
        new = FakeSocket()
        await manager.connect("AGT-001", old)
        await manager.connect("AGT-001", new)

        assert old.closed == [(4001, "replaced by newer connection")]
        assert await manager.disconnect("AGT-001", old) is False
        assert await manager.is_connected("AGT-001") is True
        assert await manager.list_connected() == ("AGT-001",)

    asyncio.run(scenario())


def test_ack_event_presence_roundtrip_and_identity_spoof_rejection() -> None:
    async def scenario() -> None:
        backend = FakeBackend()
        presence_store = PresenceStore()
        router = GatewayRouter(
            "GW-A", AgentConnectionManager(), presence_store, backend
        )
        ack = Ack(
            protocol_version=2,
            ack_id="ACK-001",
            command_id="CMD-001",
            status=AckStatus.SUCCEEDED,
            service_version="1.1.0",
            occurred_at=NOW,
            correlation_id="CORR-001",
        )
        envelope = GatewayEnvelope(
            protocol_version=2,
            message_type=GatewayMessageType.ACK,
            message_id="ACK-001",
            correlation_id="CORR-001",
            source_id="AGT-001",
            target_id="backend",
            payload=ack.model_dump(mode="json"),
        )
        await router.route_agent("AGT-001", envelope)
        assert backend.messages[-1] is envelope

        event = Event(
            protocol_version=2,
            event_id="EVT-001",
            session_id="SES-001",
            agent_id="AGT-001",
            event_type=EventType.SERVICE_HEALTH,
            occurred_at=NOW,
            payload={},
            correlation_id="CORR-002",
        )
        event_envelope = envelope.model_copy(
            update={
                "message_type": GatewayMessageType.EVENT,
                "message_id": "EVT-001",
                "payload": event.model_dump(mode="json"),
            }
        )
        await router.route_agent("AGT-001", event_envelope)

        presence = Presence(
            protocol_version=2,
            agent_id="AGT-001",
            last_seen=NOW,
            health=PresenceHealth.ONLINE,
        )
        presence_envelope = envelope.model_copy(
            update={
                "message_type": GatewayMessageType.PRESENCE,
                "message_id": "PRS-001",
                "payload": presence.model_dump(mode="json"),
            }
        )
        await router.route_agent("AGT-001", presence_envelope)
        assert (await presence_store.get("AGT-001")).health == PresenceHealth.ONLINE

        spoofed = envelope.model_copy(update={"source_id": "AGT-002"})
        with pytest.raises(ValueError, match="connection identity"):
            await router.route_agent("AGT-001", spoofed)

    asyncio.run(scenario())


def test_gateway_backend_uplink_reconnects_after_backend_restart() -> None:
    class Socket:
        sent = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def send(self, value):
            self.sent.append(value)

        async def recv(self):
            await asyncio.Event().wait()

    async def scenario() -> None:
        attempts = 0
        socket = Socket()

        def connector(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("backend restarting")
            return socket

        connected = asyncio.Event()
        settings = GatewaySettings(
            gateway_id="GW-A",
            room_id="LAB-A",
            version="1.0.0",
            listen_host="127.0.0.1",
            listen_port=8443,
            tls_certfile=None,
            tls_keyfile=None,
            backend_ws_url="ws://backend/api/v2/gateways/ws",
            gateway_bootstrap_token="gateway-token",
            agent_bootstrap_token="agent-token",
            allow_plaintext_ws=True,
            reconnect_initial_seconds=0.01,
            reconnect_max_seconds=0.02,
            presence_timeout_seconds=15,
            health_interval_seconds=0.01,
        )
        uplink = BackendUplink(
            settings,
            lambda _message: asyncio.sleep(0),
            lambda: _set_event(connected),
            lambda: _connected_count(),
            jitter=lambda: 0,
            connector=connector,
        )
        await uplink.start()
        await asyncio.wait_for(connected.wait(), timeout=1)
        deadline = asyncio.get_running_loop().time() + 0.5
        message_types = []
        while (
            GatewayMessageType.GATEWAY_HEALTH not in message_types
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.01)
            message_types = [
                GatewayEnvelope.model_validate_json(value).message_type
                for value in socket.sent
            ]
        assert attempts >= 2
        assert uplink.status == PresenceHealth.ONLINE
        assert GatewayMessageType.GATEWAY_HEALTH in message_types
        await uplink.stop()

    async def _set_event(event: asyncio.Event) -> None:
        event.set()

    async def _connected_count() -> int:
        return 3

    asyncio.run(scenario())
