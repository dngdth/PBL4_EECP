from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.application.security import Principal, Role
from app.infrastructure.security import verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
def login(body: LoginRequest, request: Request) -> dict[str, str | int]:
    settings = request.app.state.settings
    valid_user = settings.examiner_username == body.username
    valid_password = bool(
        settings.examiner_password_hash
        and verify_password(body.password, settings.examiner_password_hash)
    )
    if not valid_user or not valid_password:
        request.app.state.container.security_audit.record(
            action="AUTHENTICATION_DENIED",
            actor=body.username,
            actor_type="EXAMINER",
            resource_type="AUTH_SESSION",
            resource_id="examiner-login",
            reason_code="INVALID_CREDENTIALS",
            correlation_id=request.headers.get("x-correlation-id"),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    scope = frozenset(
        item.strip()
        for item in request.app.state.examiner_session_scope.split(",")
        if item.strip()
    )
    principal = Principal(body.username, Role(settings.examiner_role), scope)
    return {
        "access_token": request.app.state.token_service.issue(principal),
        "token_type": "bearer",
        "expires_in": 3600,
    }
