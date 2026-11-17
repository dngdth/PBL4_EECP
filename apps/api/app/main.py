from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.application.security import AuthorizationService
from app.config import Settings
from app.infrastructure.di.container import build_container
from app.infrastructure.gateway_connections import GatewayConnectionRegistry
from app.infrastructure.presence import (
    MemoryPresenceStore,
    MemoryTokenRevocationStore,
    RedisPresenceStore,
    RedisTokenRevocationStore,
)
from app.infrastructure.screen_store import ScreenStore
from app.infrastructure.security import TokenService
from app.presentation.api.deps import auth_enforced, hydrate_principal
from app.presentation.api.exceptions import register_exception_handlers
from app.presentation.api.routers.agents import router as agents_router
from app.presentation.api.routers.auth import router as auth_router
from app.presentation.api.routers.exam_sessions import router
from app.presentation.api.routers.gateways import router as gateways_router
from app.presentation.api.routers.policies import router as policies_router
from app.presentation.api.routers.screen import router as screen_router
from app.presentation.api.routers.users import router as users_router
from app.presentation.realtime import (
    RealtimeHub,
    realtime_notification_middleware,
)
from app.presentation.realtime import (
    router as realtime_router,
)


def create_app(database_path: str | Path | None = None) -> FastAPI:
    settings = Settings.from_env()
    if database_path is not None:
        settings = Settings(
            database_path=Path(database_path).resolve(),
            gateway_bootstrap_token=settings.gateway_bootstrap_token,
            environment="test",
        )
    container = build_container(settings)
    screen_store = ScreenStore(
        container.database,
        Path(os.getenv("EECP_SCREEN_STORAGE_PATH", str(settings.database_path.parent / "screens"))),
        retention_days=int(os.getenv("EECP_SCREEN_SERVER_RETENTION_DAYS", "14")),
        max_bytes=int(os.getenv("EECP_SCREEN_SERVER_MAX_BYTES", str(10 * 1024 * 1024 * 1024))),
    )

    async def screen_cleanup():
        while True:
            with suppress(OSError):
                await asyncio.to_thread(screen_store.expire)
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(screen_cleanup(), name="screen-retention-cleanup")
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(
        title="Exam Environment Control Platform",
        version="0.1.0",
        description="Clean Architecture vertical pipeline for policy-based exam control.",
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.settings = settings
    app.state.screen_store = screen_store
    ingest_concurrency = max(1, int(os.getenv("EECP_SCREEN_INGEST_CONCURRENCY", "8")))
    app.state.screen_ingest_slots = asyncio.Semaphore(ingest_concurrency)
    app.state.realtime_hub = RealtimeHub()
    app.state.gateway_connections = GatewayConnectionRegistry()
    app.state.presence = (
        RedisPresenceStore(settings.redis_url, settings.presence_ttl_seconds)
        if settings.redis_url
        else MemoryPresenceStore(settings.presence_ttl_seconds)
    )
    app.state.examiner_session_scope = os.getenv("EECP_EXAMINER_SESSION_SCOPE", "")
    app.state.token_service = (
        TokenService(
            settings.auth_signing_key,
            lifetime_seconds=settings.token_lifetime_seconds,
        )
        if settings.auth_signing_key
        else None
    )
    app.state.token_revocation = (
        RedisTokenRevocationStore(settings.redis_url)
        if settings.redis_url
        else MemoryTokenRevocationStore()
    )
    app.state.authorization = AuthorizationService()
    app.include_router(router)
    app.include_router(policies_router)
    app.include_router(screen_router)
    app.include_router(agents_router)
    app.include_router(gateways_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(realtime_router)
    app.middleware("http")(realtime_notification_middleware)
    register_exception_handlers(app)

    @app.middleware("http")
    async def authenticate_examiner(request: Request, call_next):
        examiner_api = request.url.path.startswith(
            ("/api/v1", "/api/v2/gateways")
        )
        if (
            not auth_enforced(request.app.state.settings)
            or not examiner_api
            or request.url.path == "/api/v1/auth/login"
        ):
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        token_service = request.app.state.token_service
        if not authorization.startswith("Bearer ") or token_service is None:
            container.security_audit.record(
                action="AUTHENTICATION_DENIED",
                actor="anonymous",
                actor_type="EXAMINER",
                resource_type="HTTP_API",
                resource_id=request.url.path,
                reason_code="MISSING_TOKEN",
                correlation_id=request.headers.get("x-correlation-id"),
            )
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        try:
            verified = token_service.verify(authorization[7:])
            request.state.principal = hydrate_principal(request.app, verified)
        except ValueError as exc:
            err_msg = str(exc)
            reason = (
                "REVOKED_OR_INACTIVE_TOKEN"
                if "revoked" in err_msg or "inactive" in err_msg
                else "INVALID_OR_EXPIRED_TOKEN"
            )
            container.security_audit.record(
                action="AUTHENTICATION_DENIED",
                actor="unknown-token-subject",
                actor_type="EXAMINER",
                resource_type="HTTP_API",
                resource_id=request.url.path,
                reason_code=reason,
                correlation_id=request.headers.get("x-correlation-id"),
            )
            return JSONResponse({"detail": "invalid or expired token"}, status_code=401)
        return await call_next(request)

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        database_ok = getattr(container.database, "health", lambda: True)()
        redis_ok = app.state.presence.health()
        return {
            "status": "ok" if database_ok and redis_ok else "degraded",
            "database": "online" if database_ok else "offline",
            "redis": "online" if redis_ok else "offline",
        }

    return app


app = create_app()

