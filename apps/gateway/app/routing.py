from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from apps.gateway.app.connections import AgentConnectionManager
from apps.gateway.app.presence import PresenceStore
from contracts.v2 import (
    Ack,
    AgentHello,
    Command,
    DeliveryFailure,
    Event,
    GatewayEnvelope,
    GatewayMessageType,
    Presence,
    PresenceHealth,
)


class BackendSender(Protocol):
    async def send(self, envelope: GatewayEnvelope) -> None: ...


class GatewayRouter:
    def __init__(
        self,
        gateway_id: str,
        connections: AgentConnectionManager,
        presence: PresenceStore,
        backend: BackendSender,
    ):
        self._gateway_id = gateway_id
        self._connections = connections
        self._presence = presence
        self._backend = backend
        self._agent_hellos: dict[str, AgentHello] = {}

    async def agent_connected(self, hello: AgentHello) -> None:
        self._agent_hellos[hello.agent_id] = hello
        await self._backend.send(
            self._envelope(
                GatewayMessageType.AGENT_HELLO,
                f"hello_{hello.agent_id}",
                hello.agent_id,
                hello.model_dump(mode="json", exclude_none=True),
            )
        )
        await self._report_presence(hello.agent_id, PresenceHealth.ONLINE)

    async def agent_disconnected(self, agent_id: str) -> None:
        await self._report_presence(agent_id, PresenceHealth.OFFLINE)

    async def route_agent(
        self, bound_agent_id: str, envelope: GatewayEnvelope
    ) -> None:
        if envelope.source_id != bound_agent_id:
            raise ValueError("connection identity does not match message source_id")
        if envelope.message_type == GatewayMessageType.ACK:
            Ack.model_validate(envelope.payload)
        elif envelope.message_type == GatewayMessageType.EVENT:
            event = Event.model_validate(envelope.payload)
            if event.agent_id != bound_agent_id:
                raise ValueError("event agent_id does not match connection identity")
        elif envelope.message_type == GatewayMessageType.PRESENCE:
            presence = Presence.model_validate(envelope.payload)
            if presence.agent_id != bound_agent_id:
                raise ValueError("presence agent_id does not match connection identity")
            await self._presence.put(presence)
            await self._connections.touch(bound_agent_id)
        else:
            raise ValueError("agent message type is not allowed")
        await self._backend.send(envelope)

    async def route_backend(self, envelope: GatewayEnvelope) -> None:
        if envelope.message_type != GatewayMessageType.COMMAND:
            raise ValueError("backend message type is not routable")
        command = Command.model_validate(envelope.payload)
        if envelope.target_id != command.target_id:
            raise ValueError("command target does not match envelope target")
        delivered = await self._connections.send(command.target_id, envelope.to_json())
        if delivered:
            return
        failure = DeliveryFailure(
            protocol_version=2,
            command_id=command.command_id,
            agent_id=command.target_id,
            reason="target agent is not connected to this gateway",
        )
        await self._backend.send(
            self._envelope(
                GatewayMessageType.DELIVERY_FAILURE,
                f"delivery_{command.command_id}",
                self._gateway_id,
                failure.model_dump(mode="json"),
                target_id="backend",
                correlation_id=command.correlation_id,
            )
        )

    async def announce_current_agents(self) -> None:
        for agent_id in await self._connections.list_connected():
            hello = self._agent_hellos.get(agent_id)
            if hello is not None:
                await self._backend.send(
                    self._envelope(
                        GatewayMessageType.AGENT_HELLO,
                        f"hello_{agent_id}",
                        agent_id,
                        hello.model_dump(mode="json", exclude_none=True),
                    )
                )
            await self._report_presence(agent_id, PresenceHealth.ONLINE)

    async def refresh_presence(self, timeout_seconds: float) -> None:
        now = datetime.now(UTC)
        for agent_id in await self._connections.list_connected():
            last_seen = await self._connections.last_seen(agent_id)
            current = await self._presence.get(agent_id)
            if (
                last_seen is not None
                and (now - last_seen).total_seconds() > timeout_seconds
                and current is not None
                and current.health == PresenceHealth.ONLINE
            ):
                await self._report_presence(agent_id, PresenceHealth.DEGRADED)

    async def _report_presence(
        self, agent_id: str, health: PresenceHealth
    ) -> None:
        presence = Presence(
            protocol_version=2,
            agent_id=agent_id,
            gateway_id=self._gateway_id,
            last_seen=datetime.now(UTC),
            health=health,
        )
        await self._presence.put(presence)
        await self._backend.send(
            self._envelope(
                GatewayMessageType.PRESENCE,
                f"presence_{agent_id}_{int(presence.last_seen.timestamp() * 1000)}",
                agent_id,
                presence.model_dump(mode="json", exclude_none=True),
            )
        )

    def _envelope(
        self,
        message_type: GatewayMessageType,
        message_id: str,
        source_id: str,
        payload: dict,
        *,
        target_id: str = "backend",
        correlation_id: str | None = None,
    ) -> GatewayEnvelope:
        return GatewayEnvelope(
            protocol_version=2,
            message_type=message_type,
            message_id=message_id,
            correlation_id=correlation_id or message_id,
            source_id=source_id,
            target_id=target_id,
            payload=payload,
        )
