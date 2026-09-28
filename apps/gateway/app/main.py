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
from apps.gateway.app.presence import PresenceStore
from apps.gateway.app.routing import GatewayRouter
from contracts.v2 import GatewayHealth, PresenceHealth


def create_app(settings: GatewaySettings, uplink: BackendUplink | None = None) -> FastAPI:
    connections = AgentConnectionManager()
    presence = PresenceStore()
    router_holder: dict[str, GatewayRouter] = {}

    async def connected_count() -> int:
        return len(await connections.list_connected())

    actual_uplink = uplink or BackendUplink(
        settings,
        lambda envelope: router_holder["router"].route_backend(envelope),
        lambda: router_holder["router"].announce_current_agents(),
        connected_count,
    )
    gateway_router = GatewayRouter(
        settings.gateway_id, connections, presence, actual_uplink
    )
    router_holder["router"] = gateway_router

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await actual_uplink.start()
        presence_task = asyncio.create_task(
            _presence_loop(gateway_router, settings.presence_timeout_seconds),
            name="gateway-presence-monitor",
        )
        try:
            yield
        finally:
            presence_task.cancel()
            await asyncio.gather(presence_task, return_exceptions=True)
            await actual_uplink.stop()

    app = FastAPI(title="EECP Local Gateway", version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.connections = connections
    app.state.presence = presence
    app.state.uplink = actual_uplink
    app.state.router = gateway_router
    app.include_router(agent_router)

    @app.get("/health")
    async def health() -> dict:
        connected = await connections.list_connected()
        value = GatewayHealth(
            protocol_version=2,
            gateway_id=settings.gateway_id,
            room_id=settings.room_id,
            status=PresenceHealth.ONLINE,
            last_seen=datetime.now(UTC),
            connected_agent_count=len(connected),
            backend_uplink_status=actual_uplink.status,
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


async def _presence_loop(router: GatewayRouter, timeout_seconds: float) -> None:
    while True:
        await asyncio.sleep(max(0.1, timeout_seconds / 2))
        await router.refresh_presence(timeout_seconds)


if __name__ == "__main__":
    main()
