from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    EXAMINER = "EXAMINER"


class Permission(StrEnum):
    VIEW_SESSION = "VIEW_SESSION"
    VIEW_AGENT = "VIEW_AGENT"
    VIEW_INCIDENT = "VIEW_INCIDENT"
    CONTROL_AGENT = "CONTROL_AGENT"
    MANAGE_POLICY = "MANAGE_POLICY"
    CREATE_SESSION = "CREATE_SESSION"
    MANAGE_USERS = "MANAGE_USERS"


ROLE_PERMISSIONS = {
    Role.ADMIN: frozenset(Permission),
    Role.EXAMINER: frozenset(
        {
            Permission.VIEW_SESSION,
            Permission.VIEW_AGENT,
            Permission.VIEW_INCIDENT,
            Permission.CONTROL_AGENT,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    role: Role
    session_scope: frozenset[str]
    user_id: str | None = field(default=None, compare=False)
    full_name: str | None = field(default=None, compare=False)
    jti: str | None = field(default=None, compare=False)
    expires_at: datetime | None = field(default=None, compare=False)


class AuthorizationDeniedError(PermissionError):
    pass


class AuthorizationService:
    def require(
        self,
        principal: Principal,
        permission: Permission,
        *,
        session_id: str | None = None,
        session_state: str | None = None,
    ) -> None:
        if permission not in ROLE_PERMISSIONS[principal.role]:
            raise AuthorizationDeniedError("role does not grant permission")
        if (
            session_id
            and principal.role != Role.ADMIN
            and session_id not in principal.session_scope
        ):
            raise AuthorizationDeniedError("resource is outside Examiner session scope")
        if permission == Permission.CONTROL_AGENT and session_state in {"COMPLETED", "FINISHED"}:
            raise AuthorizationDeniedError("session state does not allow control")
