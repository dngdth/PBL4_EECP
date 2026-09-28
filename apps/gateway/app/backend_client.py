from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime

from websockets.asyncio.client import connect

from apps.gateway.app.config import GatewaySettings
from contracts.v2 import (
    GatewayEnvelope,
    GatewayHealth,
    GatewayHello,
    GatewayMessageType,
    PresenceHealth,
)

MessageHandler = Callable[[GatewayEnvelope], Awaitable[None]]
ConnectedHandler = Callable[[], Awaitable[None]]
ConnectedCount = Callable[[], Awaitable[int]]
HealthFields = Callable[[], dict]


class BackendUplink:
    """Persistent Gateway-to-Backend uplink with bounded exponential reconnect."""

    def __init__(
        self,
        settings: GatewaySettings,
        on_message: MessageHandler,
        on_connected: ConnectedHandler,
        connected_agent_count: ConnectedCount,
        *,
        health_fields: HealthFields | None = None,
        jitter: Callable[[], float] = random.random,
        connector=connect,
    ):
        self._settings = settings
        self._on_message = on_message
        self._on_connected = on_connected
        self._connected_agent_count = connected_agent_count
        self._health_fields = health_fields or (lambda: {})
        self._jitter = jitter
        self._connector = connector
        self._outbound: asyncio.Queue[GatewayEnvelope] = asyncio.Queue(
            maxsize=settings.backend_queue_max_messages
        )
        self._queue_saturation_count = 0
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.status = PresenceHealth.OFFLINE

    async def start(self) -> None:
        if self._task is None:
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run(), name="gateway-backend-uplink")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self.status = PresenceHealth.OFFLINE

    async def send(self, envelope: GatewayEnvelope) -> None:
        try:
            self._outbound.put_nowait(envelope)
        except asyncio.QueueFull as exc:
            self._queue_saturation_count += 1
            raise OSError("Gateway uplink queue is full; message was not retained") from exc

    @property
    def outbound_queue_depth(self) -> int:
        return self._outbound.qsize()

    @property
    def outbound_queue_capacity(self) -> int:
        return self._outbound.maxsize

    @property
    def queue_saturation_count(self) -> int:
        return self._queue_saturation_count

    async def _run(self) -> None:
        delay = self._settings.reconnect_initial_seconds
        url = f"{self._settings.backend_ws_url}/{self._settings.gateway_id}"
        headers = {
            "Authorization": f"Bearer {self._settings.gateway_bootstrap_token}"
        }
        while not self._stop_event.is_set():
            try:
                async with self._connector(
                    url, additional_headers=headers
                ) as websocket:
                    await websocket.send(self._hello().to_json())
                    self.status = PresenceHealth.ONLINE
                    delay = self._settings.reconnect_initial_seconds
                    await self._on_connected()
                    while not self._stop_event.is_set():
                        receive_task = asyncio.create_task(websocket.recv())
                        send_task = asyncio.create_task(self._outbound.get())
                        health_task = asyncio.create_task(
                            asyncio.sleep(self._settings.health_interval_seconds)
                        )
                        tasks = {receive_task, send_task, health_task}
                        try:
                            done, _pending = await asyncio.wait(
                                tasks,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                        finally:
                            for task in tasks:
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                        if receive_task in done:
                            message = receive_task.result()
                            await self._on_message(
                                GatewayEnvelope.model_validate_json(message)
                            )
                        if send_task in done:
                            envelope = send_task.result()
                            await websocket.send(envelope.to_json())
                        if health_task in done:
                            count = await self._connected_agent_count()
                            await websocket.send(self._health(count).to_json())
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status = PresenceHealth.DEGRADED
                wait = min(
                    self._settings.reconnect_max_seconds,
                    delay * (1 + 0.25 * self._jitter()),
                )
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=wait)
                delay = min(self._settings.reconnect_max_seconds, delay * 2)

    def _hello(self) -> GatewayEnvelope:
        hello = GatewayHello(
            protocol_version=2,
            gateway_id=self._settings.gateway_id,
            room_id=self._settings.room_id,
            gateway_version=self._settings.version,
        )
        return GatewayEnvelope(
            protocol_version=2,
            message_type=GatewayMessageType.GATEWAY_HELLO,
            message_id=f"hello_{self._settings.gateway_id}",
            correlation_id=f"hello_{self._settings.gateway_id}",
            source_id=self._settings.gateway_id,
            target_id="backend",
            payload=hello.model_dump(mode="json"),
        )

    def _health(self, connected_agent_count: int) -> GatewayEnvelope:
        fields = self._health_fields()
        health = GatewayHealth(
            protocol_version=2,
            gateway_id=self._settings.gateway_id,
            room_id=self._settings.room_id,
            status=(
                PresenceHealth.DEGRADED
                if self._queue_saturation_count
                else PresenceHealth.ONLINE
            ),
            last_seen=datetime.now(UTC),
            connected_agent_count=connected_agent_count,
            backend_uplink_status=PresenceHealth.ONLINE,
            pending_event_count=fields.get("pending_event_count"),
            oldest_pending_event_age=fields.get("oldest_pending_event_age"),
            last_flush_success_at=fields.get("last_flush_success_at"),
            last_flush_error=fields.get("last_flush_error"),
            buffer_status=fields.get("buffer_status"),
            outbound_queue_depth=self.outbound_queue_depth,
            outbound_queue_capacity=self.outbound_queue_capacity,
            outbound_queue_saturation_count=self.queue_saturation_count,
        )
        return GatewayEnvelope(
            protocol_version=2,
            message_type=GatewayMessageType.GATEWAY_HEALTH,
            message_id=f"health_{int(health.last_seen.timestamp() * 1000)}",
            correlation_id=f"health_{self._settings.gateway_id}",
            source_id=self._settings.gateway_id,
            target_id="backend",
            payload=health.model_dump(mode="json"),
        )
