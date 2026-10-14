import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.infrastructure.gateway_connections import GatewayConnectionRegistry

from apps.api.test.integration.test_local_gateway_agent_ws import _settings
from apps.api.test.unit.test_gateway_phase5 import FakeSocket, _command_envelope
from apps.gateway.app.application.presence_monitor import PresenceMonitor
from apps.gateway.app.application.routing import GatewayRouter
from apps.gateway.app.backend_client import BackendUplink
from apps.gateway.app.connections import AgentConnectionManager
from apps.gateway.app.presence import PresenceStore
from contracts.v2 import AgentHello, GatewayEnvelope, GatewayMessageType, Presence, PresenceHealth


def _presence() -> GatewayEnvelope:
    value = Presence(
        protocol_version=2,
        agent_id="AGT-001",
        last_seen=datetime.now(UTC),
        health=PresenceHealth.ONLINE,
    )
    return GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.PRESENCE,
        message_id="presence-1",
        correlation_id="presence-1",
        source_id="AGT-001",
        target_id="backend",
        payload=value.model_dump(mode="json"),
    )


class Socket:
    def __init__(self, *, fail_presence=False):
        self.messages = []
        self.fail_presence = fail_presence

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def recv(self):
        await asyncio.Event().wait()

    async def send(self, raw):
        envelope = GatewayEnvelope.model_validate_json(raw)
        if envelope.message_type == GatewayMessageType.PRESENCE and self.fail_presence:
            raise OSError("connection lost during send")
        self.messages.append(envelope)


def test_health_is_periodic_while_outbound_traffic_is_continuous() -> None:
    async def scenario():
        socket = Socket()
        connected = asyncio.Event()

        async def on_connected():
            connected.set()

        uplink = BackendUplink(
            replace(_settings(), health_interval_seconds=0.02),
            lambda _: asyncio.sleep(0),
            on_connected,
            lambda: asyncio.sleep(0, result=30),
            connector=lambda *a, **k: socket,
        )
        await uplink.start()
        try:
            await asyncio.wait_for(connected.wait(), 1)
            end = asyncio.get_running_loop().time() + 0.2
            while asyncio.get_running_loop().time() < end:
                await uplink.send(_presence())
                await asyncio.sleep(0.001)
            health = [
                e for e in socket.messages if e.message_type == GatewayMessageType.GATEWAY_HEALTH
            ]
            assert len(health) >= 3
            assert all(e.payload["connected_agent_count"] == 30 for e in health)
        finally:
            await uplink.stop()

    asyncio.run(scenario())


def test_failed_send_is_retried_after_backend_reconnect() -> None:
    async def scenario():
        failed, recovered = Socket(fail_presence=True), Socket()
        attempts = 0

        def connector(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            return failed if attempts == 1 else recovered

        uplink = BackendUplink(
            _settings(),
            lambda _: asyncio.sleep(0),
            lambda: asyncio.sleep(0),
            lambda: asyncio.sleep(0, result=1),
            connector=connector,
        )
        envelope = _presence()
        await uplink.send(envelope)
        await uplink.start()
        try:
            async with asyncio.timeout(2):
                while not any(
                    e.message_type == GatewayMessageType.PRESENCE for e in recovered.messages
                ):
                    await asyncio.sleep(0.01)
            assert attempts >= 2
            assert envelope in recovered.messages
            assert uplink.outbound_queue_depth == 0
        finally:
            await uplink.stop()

    asyncio.run(scenario())


def test_backpressure_keeps_local_presence_and_publishes_latest_state_on_recovery() -> None:
    class Backend:
        blocked = True
        messages = []

        async def send(self, envelope):
            if self.blocked:
                raise OSError("queue full")
            self.messages.append(envelope)

    async def scenario():
        backend = Backend()
        connections = AgentConnectionManager()
        presence = PresenceStore()
        router = GatewayRouter("GW-A", connections, presence, backend)
        await connections.connect("AGT-001", FakeSocket())
        await router.agent_connected(
            AgentHello(
                protocol_version=2,
                agent_id="AGT-001",
                hostname="HOST",
                ip_address="192.0.2.1",
                agent_version="1",
            )
        )
        connections._last_seen["AGT-001"] = datetime.now(UTC) - timedelta(seconds=20)
        await router.refresh_presence(10, 30)
        assert (await presence.get("AGT-001")).health == PresenceHealth.DEGRADED
        assert await connections.is_connected("AGT-001")
        backend.blocked = False
        await router.announce_current_agents()
        assert [e.message_type for e in backend.messages] == [
            GatewayMessageType.AGENT_HELLO,
            GatewayMessageType.PRESENCE,
        ]
        assert backend.messages[-1].payload["health"] == "DEGRADED"
        await router.flush_pending_control()
        assert len(backend.messages) == 2

    asyncio.run(scenario())


def test_presence_monitor_continues_after_temporary_publication_error() -> None:
    class Refresher:
        attempts = 0

        async def refresh_presence(self, *_args):
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("queue full")

    async def scenario():
        refresher = Refresher()
        task = asyncio.create_task(PresenceMonitor(refresher, 0.01, 0.02).run())
        try:
            async with asyncio.timeout(2):
                while refresher.attempts < 2:
                    await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_closed_agent_socket_reports_delivery_failure_without_throwing() -> None:
    class ClosedSocket(FakeSocket):
        async def send_text(self, _message):
            raise RuntimeError("Cannot send after closing WebSocket")

    class Backend:
        messages = []

        async def send(self, envelope):
            self.messages.append(envelope)

    async def scenario():
        connections, backend = AgentConnectionManager(), Backend()
        await connections.connect("AGT-001", ClosedSocket())
        router = GatewayRouter("GW-A", connections, PresenceStore(), backend)
        await router.route_backend(_command_envelope("AGT-001"))
        assert backend.messages[-1].message_type == GatewayMessageType.DELIVERY_FAILURE
        assert not await connections.is_connected("AGT-001")

    asyncio.run(scenario())


def test_health_recovers_when_queue_drains_but_keeps_overflow_counter() -> None:
    async def scenario():
        uplink = BackendUplink(
            replace(_settings(), backend_queue_max_messages=1),
            lambda _: asyncio.sleep(0),
            lambda: asyncio.sleep(0),
            lambda: asyncio.sleep(0, result=1),
        )
        await uplink.send(_presence())
        with pytest.raises(OSError):
            await uplink.send(_presence())
        assert uplink._health(1).payload["status"] == "DEGRADED"
        await uplink._outbound.get()
        assert uplink._health(1).payload["status"] == "ONLINE"
        assert uplink._health(1).payload["outbound_queue_saturation_count"] == 1

    asyncio.run(scenario())


def test_stale_gateway_disconnect_cannot_remove_or_mark_new_connection_offline() -> None:
    async def scenario():
        registry = GatewayConnectionRegistry()
        old, new = FakeSocket(), FakeSocket()
        await registry.connect("GW-A", old)
        await registry.connect("GW-A", new)
        assert old.closed == [(4001, "replaced by newer Gateway connection")]
        assert await registry.disconnect("GW-A", old) is False
        assert await registry.is_connected("GW-A")
        assert await registry.send("GW-A", "message")
        assert new.sent == ["message"]
        assert await registry.disconnect("GW-A", new) is True

    asyncio.run(scenario())
