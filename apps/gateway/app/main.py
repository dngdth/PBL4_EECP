from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import uvicorn
from fastapi import FastAPI

from apps.gateway.app.agent_ws import router as agent_router
from apps.gateway.app.backend_client import BackendUplink
from apps.gateway.app.config import GatewaySettings
from apps.gateway.app.connections import AgentConnectionManager
from apps.gateway.app.event_buffer import EventBuffer
from apps.gateway.app.event_flusher import EventFlusher
from apps.gateway.app.presence import PresenceStore
from apps.gateway.app.routing import GatewayRouter
from contracts.v2 import GatewayHealth, PresenceHealth


def create_app(
    settings: GatewaySettings,
    uplink: BackendUplink | None = None,
    event_buffer: EventBuffer | None = None,
) -> FastAPI:
    connections = AgentConnectionManager()
    presence = PresenceStore()
    router_holder: dict[str, GatewayRouter] = {}
    flusher_holder: dict[str, EventFlusher] = {}
    buffer_holder: dict[str, EventBuffer] = {}

    async def connected_count() -> int:
        return len(await connections.list_connected())

    async def on_connected() -> None:
        await router_holder["router"].announce_current_agents()
        flusher = flusher_holder.get("flusher")
        if flusher is not None:
            flusher.wake()

    def health_fields() -> dict:
        buffer = buffer_holder.get("buffer")
        if buffer is None:
            return {}
        health = buffer.health()
        return {
            "pending_event_count": health.pending_event_count,
            "oldest_pending_event_age": health.oldest_pending_event_age,
            "last_flush_success_at": health.last_flush_success_at,
            "last_flush_error": health.last_flush_error,
            "buffer_status": health.buffer_status,
        }

    actual_uplink = uplink or BackendUplink(
        settings,
        lambda envelope: router_holder["router"].route_backend(envelope),
        on_connected,
        connected_count,
        health_fields=health_fields,
    )
    actual_buffer = event_buffer
    if actual_buffer is None and settings.event_buffer_path:
        actual_buffer = EventBuffer(
            settings.event_buffer_path,
            max_rows=settings.event_buffer_max_rows,
            degraded_threshold=settings.event_buffer_degraded_threshold,
        )
    if actual_buffer is not None:
        buffer_holder["buffer"] = actual_buffer
    event_flusher = None
    if actual_buffer is not None:
        event_flusher = EventFlusher(
            actual_buffer,
            actual_uplink,
            initial_delay_seconds=settings.event_retry_initial_seconds,
            max_delay_seconds=settings.event_retry_max_seconds,
            jitter_ratio=settings.event_retry_jitter_ratio,
            batch_size=settings.event_flush_batch_size,
            poll_interval_seconds=settings.event_flush_interval_seconds,
        )
        flusher_holder["flusher"] = event_flusher
    gateway_router = GatewayRouter(
        settings.gateway_id,
        connections,
        presence,
        actual_uplink,
        actual_buffer,
        event_flusher,
    )
    router_holder["router"] = gateway_router

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await actual_uplink.start()
        if event_flusher is not None:
            await event_flusher.start()
        presence_task = asyncio.create_task(
            _presence_loop(
                gateway_router,
                settings.presence_timeout_seconds,
                settings.presence_offline_timeout_seconds,
            ),
            name="gateway-presence-monitor",
        )
        try:
            yield
        finally:
            presence_task.cancel()
            await asyncio.gather(presence_task, return_exceptions=True)
            if event_flusher is not None:
                await event_flusher.stop()
            await actual_uplink.stop()

    app = FastAPI(title="EECP Local Gateway", version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.connections = connections
    app.state.presence = presence
    app.state.uplink = actual_uplink
    app.state.router = gateway_router
    app.state.event_buffer = actual_buffer
    app.state.event_flusher = event_flusher
    app.include_router(agent_router)

    @app.get("/health")
    async def health() -> dict:
        connected = await connections.list_connected()
        buffer_health = actual_buffer.health() if actual_buffer is not None else None
        status_value = (
            PresenceHealth.DEGRADED
            if buffer_health is not None
            and buffer_health.buffer_status == PresenceHealth.DEGRADED
            else PresenceHealth.ONLINE
        )
        value = GatewayHealth(
            protocol_version=2,
            gateway_id=settings.gateway_id,
            room_id=settings.room_id,
            status=status_value,
            last_seen=datetime.now(UTC),
            connected_agent_count=len(connected),
            backend_uplink_status=actual_uplink.status,
            pending_event_count=(
                buffer_health.pending_event_count if buffer_health is not None else 0
            ),
            oldest_pending_event_age=(
                buffer_health.oldest_pending_event_age if buffer_health is not None else None
            ),
            last_flush_success_at=(
                buffer_health.last_flush_success_at if buffer_health is not None else None
            ),
            last_flush_error=(
                buffer_health.last_flush_error if buffer_health is not None else None
            ),
            buffer_status=(
                buffer_health.buffer_status
                if buffer_health is not None
                else PresenceHealth.ONLINE
            ),
        )
        return value.model_dump(mode="json")

    return app


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="EECP Local Gateway")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        settings = GatewaySettings.from_env()
    except ValueError as exc:
        raise SystemExit(f"Gateway configuration error: {exc}") from None
    if args.check:
        print(
            f"component=local-gateway gateway_id={settings.gateway_id} "
            f"room_id={settings.room_id} backend={settings.backend_ws_url}"
        )
        return
    uvicorn.run(
        create_app(settings),
        host=settings.listen_host,
        port=settings.listen_port,
        ssl_certfile=settings.tls_certfile,
        ssl_keyfile=settings.tls_keyfile,
    )


async def _presence_loop(
    router: GatewayRouter, stale_seconds: float, offline_seconds: float
) -> None:
    while True:
        await asyncio.sleep(max(0.1, stale_seconds / 2))
        await router.refresh_presence(stale_seconds, offline_seconds)


if __name__ == "__main__":
    main()
