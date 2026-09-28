from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.application.security import AuthorizationService
from app.config import Settings
from app.infrastructure.di.container import build_container
from app.infrastructure.gateway_connections import GatewayConnectionRegistry
from app.infrastructure.presence import MemoryPresenceStore, RedisPresenceStore
from app.infrastructure.security import TokenService
from app.presentation.api.exceptions import register_exception_handlers
from app.presentation.api.routers.agents import router as agents_router
from app.presentation.api.routers.auth import router as auth_router
from app.presentation.api.routers.exam_sessions import router
from app.presentation.api.routers.gateways import router as gateways_router
from app.presentation.api.routers.policies import router as policies_router


def create_app(database_path: str | Path | None = None) -> FastAPI:
    settings = Settings.from_env()
    if database_path is not None:
        settings = Settings(
            database_path=Path(database_path).resolve(),
            gateway_bootstrap_token=settings.gateway_bootstrap_token,
            environment="test",
        )
    container = build_container(settings)

    app = FastAPI(
        title="Exam Environment Control Platform",
        version="0.1.0",
        description="Clean Architecture vertical pipeline for policy-based exam control.",
    )
    app.state.container = container
    app.state.settings = settings
    app.state.gateway_connections = GatewayConnectionRegistry()
    app.state.presence = (
        RedisPresenceStore(settings.redis_url, settings.presence_ttl_seconds)
        if settings.redis_url
        else MemoryPresenceStore(settings.presence_ttl_seconds)
    )
    app.state.examiner_session_scope = os.getenv("EECP_EXAMINER_SESSION_SCOPE", "")
    app.state.token_service = (
        TokenService(settings.auth_signing_key)
        if settings.auth_signing_key
        else None
    )
    app.state.authorization = AuthorizationService()
    app.include_router(router)
    app.include_router(policies_router)
    app.include_router(agents_router)
    app.include_router(gateways_router)
    app.include_router(auth_router)
    register_exception_handlers(app)

    @app.middleware("http")
    async def authenticate_examiner(request: Request, call_next):
        if (
            settings.environment != "production-like"
            or not (
                request.url.path.startswith("/api/v1")
                or request.url.path == "/api/v2/gateways"
            )
            or request.url.path == "/api/v1/auth/login"
        ):
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or app.state.token_service is None:
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
            request.state.principal = app.state.token_service.verify(authorization[7:])
        except ValueError:
            container.security_audit.record(
                action="AUTHENTICATION_DENIED",
                actor="unknown-token-subject",
                actor_type="EXAMINER",
                resource_type="HTTP_API",
                resource_id=request.url.path,
                reason_code="INVALID_OR_EXPIRED_TOKEN",
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

