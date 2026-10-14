from __future__ import annotations

import asyncio
import random
import ssl
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol

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


class BackendSocket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...


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
        self._ssl_context = (
            ssl.create_default_context(cafile=settings.backend_ca_file)
            if settings.backend_ws_url.startswith("wss://")
            else None
        )
        self._pending_send: GatewayEnvelope | None = None
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
        return self._outbound.qsize() + int(self._pending_send is not None)

    @property
    def outbound_queue_capacity(self) -> int:
        return self._outbound.maxsize

    @property
    def queue_saturation_count(self) -> int:
        return self._queue_saturation_count

    async def _run(self) -> None:
        delay = self._settings.reconnect_initial_seconds
        url = f"{self._settings.backend_ws_url}/{self._settings.gateway_id}"
        headers = {"Authorization": f"Bearer {self._settings.gateway_bootstrap_token}"}
        while not self._stop_event.is_set():
            try:
                options = {"additional_headers": headers}
                if self._ssl_context is not None:
                    options["ssl"] = self._ssl_context
                async with self._connector(url, **options) as websocket:
                    await websocket.send(self._hello().to_json())
                    self.status = PresenceHealth.ONLINE
                    delay = self._settings.reconnect_initial_seconds
                    await self._on_connected()
                    await self._serve_connection(websocket)
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

    async def _serve_connection(self, websocket: BackendSocket) -> None:
        # Independent workers keep health periodic under continuous traffic. One
        # send lock serializes writes; cancelling one worker cannot consume and
        # discard another worker's queued envelope.
        send_lock = asyncio.Lock()
        tasks = {
            asyncio.create_task(self._receive(websocket), name="gateway-uplink-receive"),
            asyncio.create_task(
                self._send_queued(websocket, send_lock), name="gateway-uplink-send"
            ),
            asyncio.create_task(
                self._send_health(websocket, send_lock), name="gateway-uplink-health"
            ),
        }
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _receive(self, websocket: BackendSocket) -> None:
        while True:
            envelope = GatewayEnvelope.model_validate_json(await websocket.recv())
            await self._on_message(envelope)

    async def _send_queued(self, websocket: BackendSocket, lock: asyncio.Lock) -> None:
        while True:
            if self._pending_send is None:
                self._pending_send = await self._outbound.get()
            async with lock:
                await websocket.send(self._pending_send.to_json())
            self._pending_send = None
            # A busy socket adapter may finish send synchronously; let timers run.
            await asyncio.sleep(0)

    async def _send_health(self, websocket: BackendSocket, lock: asyncio.Lock) -> None:
        while True:
            count = await self._connected_agent_count()
            async with lock:
                await websocket.send(self._health(count).to_json())
            await asyncio.sleep(self._settings.health_interval_seconds)

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
                if self._outbound.full() or fields.get("buffer_status") == PresenceHealth.DEGRADED
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
