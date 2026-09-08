from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from app.config import Settings
from app.infrastructure.di.container import build_container
from app.presentation.api.exceptions import register_exception_handlers
from app.presentation.api.routers.agents import router as agents_router
from app.presentation.api.routers.exam_sessions import router
from app.presentation.api.routers.policies import router as policies_router


def create_app(database_path: str | Path | None = None) -> FastAPI:
    settings = Settings.from_env()
    if database_path is not None:
        settings = Settings(database_path=Path(database_path).resolve())
    container = build_container(settings)

    app = FastAPI(
        title="Exam Environment Control Platform",
        version="0.1.0",
        description="Clean Architecture vertical pipeline for policy-based exam control.",
    )
    app.state.container = container
    app.include_router(router)
    app.include_router(policies_router)
    app.include_router(agents_router)
    register_exception_handlers(app)

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()

