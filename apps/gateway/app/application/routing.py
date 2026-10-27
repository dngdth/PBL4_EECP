from __future__ import annotations

from datetime import UTC, datetime

from apps.gateway.app.application.ports import (
    AgentConnections,
    BackendSender,
    EventDelivery,
    EventStore,
    PresenceRepository,
)
from contracts.v2 import (
    Ack,
    AgentHello,
    AgentSessionState,
    Command,
    DeliveryFailure,
    Event,
    EventReceipt,
    EventReceiptStatus,
    GatewayEnvelope,
    GatewayMessageType,
    Presence,
    PresenceHealth,
)


class GatewayRouter:
    def __init__(
        self,
        gateway_id: str,
        connections: AgentConnections,
        presence: PresenceRepository,
        backend: BackendSender,
        event_buffer: EventStore | None = None,
        event_flusher: EventDelivery | None = None,
    ):
        self._gateway_id = gateway_id
        self._connections = connections
        self._presence = presence
        self._backend = backend
        self._event_buffer = event_buffer
        self._event_flusher = event_flusher
        self._agent_hellos: dict[str, AgentHello] = {}
        self._pending_hellos: set[str] = set()
        self._pending_presence: set[str] = set()

    async def agent_connected(self, hello: AgentHello) -> None:
        self._agent_hellos[hello.agent_id] = hello
        self._pending_hellos.add(hello.agent_id)
        await self._publish_hello(hello.agent_id)
        await self._report_presence(hello.agent_id, PresenceHealth.ONLINE)

    async def agent_disconnected(self, agent_id: str) -> None:
        await self._report_presence(agent_id, PresenceHealth.OFFLINE)

    async def report_security_audit(self, actor_id: str, reason_code: str) -> None:
        await self._backend.send(
            self._envelope(
                GatewayMessageType.SECURITY_AUDIT,
                f"security_{actor_id}_{int(datetime.now(UTC).timestamp() * 1000)}",
                self._gateway_id,
                {"actor_id": actor_id, "reason_code": reason_code},
                correlation_id=f"agent-auth_{actor_id}",
            )
        )

    async def route_agent(self, bound_agent_id: str, envelope: GatewayEnvelope) -> None:
        if envelope.source_id != bound_agent_id:
            raise ValueError("connection identity does not match message source_id")
        if envelope.message_type == GatewayMessageType.ACK:
            ack = Ack.model_validate(envelope.payload)
            if envelope.correlation_id != ack.correlation_id:
                raise ValueError("ACK correlation does not match envelope")
        elif envelope.message_type == GatewayMessageType.EVENT:
            event = Event.model_validate(envelope.payload)
            if event.agent_id != bound_agent_id:
                raise ValueError("event agent_id does not match connection identity")
            if event.correlation_id and envelope.correlation_id != event.correlation_id:
                raise ValueError("Event correlation does not match envelope")
            if self._event_buffer is not None:
                inserted = self._event_buffer.put(envelope)
                if self._event_flusher is not None:
                    self._event_flusher.note_buffered(inserted)
                    self._event_flusher.wake()
                receipt = EventReceipt(
                    protocol_version=2,
                    event_id=event.event_id,
                    status=(
                        EventReceiptStatus.ACCEPTED
                        if inserted
                        else EventReceiptStatus.ALREADY_PROCESSED
                    ),
                    received_at=datetime.now(UTC),
                )
                await self._connections.send(
                    bound_agent_id,
                    self._envelope(
                        GatewayMessageType.EVENT_RECEIPT,
                        f"receipt_{event.event_id}",
                        self._gateway_id,
                        receipt.model_dump(mode="json", exclude_none=True),
                        target_id=bound_agent_id,
                        correlation_id=envelope.correlation_id or event.event_id,
                    ).to_json(),
                )
                return
            await self._backend.send(envelope)
            receipt = EventReceipt(
                protocol_version=2,
                event_id=event.event_id,
                status=EventReceiptStatus.ACCEPTED,
                received_at=datetime.now(UTC),
            )
            await self._connections.send(
                bound_agent_id,
                self._envelope(
                    GatewayMessageType.EVENT_RECEIPT,
                    f"receipt_{event.event_id}",
                    self._gateway_id,
                    receipt.model_dump(mode="json", exclude_none=True),
                    target_id=bound_agent_id,
                    correlation_id=envelope.correlation_id or event.event_id,
                ).to_json(),
            )
            return
        elif envelope.message_type == GatewayMessageType.PRESENCE:
            presence = Presence.model_validate(envelope.payload)
            if presence.agent_id != bound_agent_id:
                raise ValueError("presence agent_id does not match connection identity")
            presence = presence.model_copy(update={"gateway_id": self._gateway_id})
            await self._presence.put(presence)
            await self._connections.touch(bound_agent_id)
            self._pending_presence.add(bound_agent_id)
            await self._publish_presence(bound_agent_id)
            return
        else:
            raise ValueError("agent message type is not allowed")
        await self._backend.send(envelope)

    async def route_backend(self, envelope: GatewayEnvelope) -> None:
        if envelope.message_type == GatewayMessageType.ERROR:
            # Backend validation errors are control feedback, not reconnect requests.
            return
        if envelope.message_type == GatewayMessageType.EVENT_RECEIPT:
            if self._event_flusher is None:
                raise ValueError("Gateway event receipt handler is unavailable")
            if envelope.source_id != "backend" or envelope.target_id != self._gateway_id:
                raise ValueError("Event receipt identity does not match Gateway")
            await self._event_flusher.handle_receipt(envelope)
            return
        if envelope.message_type == GatewayMessageType.AGENT_SESSION_STATE:
            state = AgentSessionState.model_validate(envelope.payload)
            if (
                envelope.source_id != "backend"
                or envelope.target_id != state.agent_id
            ):
                raise ValueError("Agent session state identity does not match envelope")
            delivered = await self._connections.send(
                state.agent_id, envelope.to_json()
            )
            if not delivered:
                raise ValueError("Agent session state target is not connected")
            return
        if envelope.message_type != GatewayMessageType.COMMAND:
            raise ValueError("backend message type is not routable")
        command = Command.model_validate(envelope.payload)
        if envelope.target_id != command.target_id:
            raise ValueError("command target does not match envelope target")
        if envelope.correlation_id != command.correlation_id:
            raise ValueError("command correlation does not match envelope")
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
            self._pending_hellos.add(agent_id)
            self._pending_presence.add(agent_id)
        await self.flush_pending_control()

    async def flush_pending_control(self) -> None:
        """Retry current identities and latest presence, without an unbounded queue."""
        for agent_id in tuple(self._pending_hellos):
            await self._publish_hello(agent_id)
        for agent_id in tuple(self._pending_presence):
            if agent_id not in self._pending_hellos:
                await self._publish_presence(agent_id)

    async def _publish_hello(self, agent_id: str) -> None:
        hello = self._agent_hellos.get(agent_id)
        if hello is None:
            self._pending_hellos.discard(agent_id)
            return
        try:
            await self._backend.send(
                self._envelope(
                    GatewayMessageType.AGENT_HELLO,
                    f"hello_{agent_id}",
                    agent_id,
                    hello.model_dump(mode="json", exclude_none=True),
                )
            )
        except OSError:
            return
        if self._agent_hellos.get(agent_id) == hello:
            self._pending_hellos.discard(agent_id)

    async def _publish_presence(self, agent_id: str) -> None:
        if agent_id in self._pending_hellos:
            return
        presence = await self._presence.get(agent_id)
        if presence is None:
            self._pending_presence.discard(agent_id)
            return
        try:
            await self._backend.send(
                self._envelope(
                    GatewayMessageType.PRESENCE,
                    f"presence_{agent_id}_{int(presence.last_seen.timestamp() * 1000)}",
                    agent_id,
                    presence.model_dump(mode="json", exclude_none=True),
                )
            )
        except OSError:
            return
        if await self._presence.get(agent_id) == presence:
            self._pending_presence.discard(agent_id)

    async def refresh_presence(
        self, stale_seconds: float, offline_seconds: float | None = None
    ) -> None:
        offline_after = offline_seconds or stale_seconds * 2
        now = datetime.now(UTC)
        for agent_id in await self._connections.list_connected():
            last_seen = await self._connections.last_seen(agent_id)
            current = await self._presence.get(agent_id)
            age = (now - last_seen).total_seconds() if last_seen is not None else 0
            if (
                current is not None
                and age > offline_after
                and current.health != PresenceHealth.OFFLINE
            ):
                await self._report_presence(agent_id, PresenceHealth.OFFLINE)
            elif (
                current is not None
                and age > stale_seconds
                and current.health == PresenceHealth.ONLINE
            ):
                await self._report_presence(agent_id, PresenceHealth.DEGRADED)
        await self.flush_pending_control()

    async def _report_presence(self, agent_id: str, health: PresenceHealth) -> None:
        presence = Presence(
            protocol_version=2,
            agent_id=agent_id,
            gateway_id=self._gateway_id,
            last_seen=datetime.now(UTC),
            health=health,
        )
        await self._presence.put(presence)
        self._pending_presence.add(agent_id)
        await self._publish_presence(agent_id)

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
