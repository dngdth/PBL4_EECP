from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities.user import UserRole


@dataclass(frozen=True, slots=True)
class CreateUserInput:
    username: str
    password: str
    full_name: str
    role: UserRole = UserRole.EXAMINER


@dataclass(frozen=True, slots=True)
class UpdateUserInput:
    user_id: str
    full_name: str | None = None
    password: str | None = None
    is_active: bool | None = None
    role: UserRole | None = None


@dataclass(frozen=True, slots=True)
class AssignSessionExaminersInput:
    session_id: str
    examiner_ids: list[str]
    actor: str = "admin"

