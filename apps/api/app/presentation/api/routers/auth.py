from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.application.security import Principal, Role
from app.domain.entities.user import UserRole
from app.infrastructure.security import verify_password
from app.presentation.api.deps import auth_enforced, hydrate_principal

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def _principal_user_payload(principal: Principal) -> dict[str, object]:
    return {
        "id": principal.user_id,
        "username": principal.subject,
        "full_name": principal.full_name or principal.subject,
        "role": principal.role.value,
        "session_scope": sorted(principal.session_scope),
    }


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


@router.post("/login")
def login(body: LoginRequest, request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    token_service = request.app.state.token_service
    container = request.app.state.container
    normalized_username = body.username.strip().lower()
    with container.database.unit_of_work() as uow:
        user = uow.users.find_by_username(normalized_username)
        if user is not None:
            if not user.is_active:
                uow.commit()
                container.security_audit.record(
                    action="AUTHENTICATION_DENIED",
                    actor=user.username,
                    actor_type=user.role.value,
                    resource_type="AUTH_SESSION",
                    resource_id="examiner-login",
                    reason_code="ACCOUNT_DISABLED",
                    correlation_id=request.headers.get("x-correlation-id"),
                )
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account is disabled")
            if not verify_password(body.password, user.password_hash):
                uow.commit()
                container.security_audit.record(
                    action="AUTHENTICATION_DENIED",
                    actor=user.username,
                    actor_type=user.role.value,
                    resource_type="AUTH_SESSION",
                    resource_id="examiner-login",
                    reason_code="INVALID_CREDENTIALS",
                    correlation_id=request.headers.get("x-correlation-id"),
                )
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
            scope = (
                frozenset()
                if user.role == UserRole.ADMIN
                else uow.users.list_session_ids_for_user(user.id)
            )
            uow.commit()
            principal = Principal(
                subject=user.username,
                role=Role(user.role.value),
                session_scope=scope,
                user_id=user.id,
                full_name=user.full_name,
            )
        else:
            uow.commit()
            valid_user = settings.examiner_username == body.username
            valid_password = bool(
                settings.examiner_password_hash
                and verify_password(body.password, settings.examiner_password_hash)
            )
            if not valid_user or not valid_password:
                container.security_audit.record(
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
            principal = Principal(
                subject=body.username,
                role=Role(settings.examiner_role),
                session_scope=scope,
                full_name=body.username,
            )

    if token_service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication signing key is not configured",
        )
    expires_in = getattr(settings, "token_lifetime_seconds", 3600)
    return {
        "access_token": token_service.issue(principal),
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": _principal_user_payload(principal),
    }


@router.get("/me")
def me(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    enforced = auth_enforced(settings)
    principal = getattr(request.state, "principal", None)
    if principal is None:
        token = _extract_bearer_token(request)
        token_service = request.app.state.token_service
        if token and token_service is not None:
            try:
                principal = hydrate_principal(request.app, token_service.verify(token))
            except ValueError as exc:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "invalid or expired token"
                ) from exc
    if principal is None:
        if enforced:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
        return {
            "authenticated": False,
            "auth_enforced": False,
            "user": None,
        }
    return {
        "authenticated": True,
        "auth_enforced": enforced,
        "user": _principal_user_payload(principal),
    }


@router.post("/refresh")
def refresh(request: Request) -> dict[str, object]:
    token_service = request.app.state.token_service
    if token_service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication signing key is not configured",
        )
    principal = getattr(request.state, "principal", None)
    if principal is None:
        token = _extract_bearer_token(request)
        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
        try:
            principal = hydrate_principal(request.app, token_service.verify(token))
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "invalid or expired token"
            ) from exc
    fresh_principal = Principal(
        subject=principal.subject,
        role=principal.role,
        session_scope=principal.session_scope,
        user_id=principal.user_id,
        full_name=principal.full_name,
    )
    expires_in = getattr(request.app.state.settings, "token_lifetime_seconds", 3600)
    return {
        "access_token": token_service.issue(fresh_principal),
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": _principal_user_payload(fresh_principal),
    }


@router.post("/logout")
def logout(request: Request) -> dict[str, str]:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        token = _extract_bearer_token(request)
        token_service = request.app.state.token_service
        if token and token_service is not None:
            try:
                principal = token_service.verify(token)
            except ValueError:
                principal = None
    if principal is not None and principal.jti:
        revocation = getattr(request.app.state, "token_revocation", None)
        if revocation is not None:
            revocation.revoke(principal.jti, principal.expires_at)
        request.app.state.container.security_audit.record(
            action="AUTH_LOGOUT",
            actor=principal.subject,
            actor_type=principal.role.value,
            resource_type="AUTH_SESSION",
            resource_id=principal.jti,
            reason_code="USER_LOGOUT",
            correlation_id=request.headers.get("x-correlation-id"),
        )
    return {"status": "logged_out"}
