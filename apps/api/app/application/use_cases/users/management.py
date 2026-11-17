from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from app.application.dtos.users import (
    AssignSessionExaminersInput,
    CreateUserInput,
    UpdateUserInput,
)
from app.domain.entities.user import (
    User,
    UserRole,
    normalize_full_name,
    normalize_username,
)
from app.domain.exceptions.errors import DomainValidationError, UserConflictError
from app.domain.interfaces.unit_of_work import UnitOfWorkFactory
from app.domain.value_objects.primitives import utc_now

Clock = Callable[[], datetime]
PasswordHasher = Callable[[str], str]


def _validate_password(password: str) -> str:
    if len(password) < 6 or len(password) > 1024:
        raise DomainValidationError("password must be between 6 and 1024 characters")
    return password


class CreateUser:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        password_hasher: PasswordHasher,
        clock: Clock = utc_now,
    ):
        self._uow_factory = uow_factory
        self._password_hasher = password_hasher
        self._clock = clock

    def __call__(self, data: CreateUserInput) -> User:
        username = normalize_username(data.username)
        full_name = normalize_full_name(data.full_name)
        password = _validate_password(data.password)
        at = self._clock()
        with self._uow_factory() as uow:
            if uow.users.find_by_username(username) is not None:
                raise UserConflictError(f"username already exists: {username}")
            user = User(
                id=f"USR-{uuid4().hex[:10].upper()}",
                username=username,
                password_hash=self._password_hasher(password),
                full_name=full_name,
                role=data.role,
                is_active=True,
                created_at=at,
                updated_at=at,
            )
            uow.users.add(user)
            uow.commit()
            return user


class ListUsers:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def __call__(
        self, *, role: UserRole | None = None, active_only: bool = False
    ) -> list[User]:
        with self._uow_factory() as uow:
            users = uow.users.list_all(role=role, active_only=active_only)
            uow.commit()
            return users


class UpdateUser:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        password_hasher: PasswordHasher,
        clock: Clock = utc_now,
    ):
        self._uow_factory = uow_factory
        self._password_hasher = password_hasher
        self._clock = clock

    def __call__(self, data: UpdateUserInput) -> User:
        at = self._clock()
        with self._uow_factory() as uow:
            current = uow.users.get(data.user_id)
            updated = current
            if data.full_name is not None:
                updated = replace(updated, full_name=normalize_full_name(data.full_name))
            if data.password is not None:
                updated = replace(
                    updated,
                    password_hash=self._password_hasher(_validate_password(data.password)),
                )
            if data.is_active is not None:
                updated = replace(updated, is_active=data.is_active)
            if data.role is not None:
                updated = replace(updated, role=data.role)
            if updated == current:
                uow.commit()
                return current
            updated = replace(updated, updated_at=at)
            uow.users.save(updated)
            uow.commit()
            return updated


class AssignSessionExaminers:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self, data: AssignSessionExaminersInput) -> list[User]:
        at = self._clock()
        with self._uow_factory() as uow:
            session = uow.sessions.get(data.session_id.strip())
            deduped = list(dict.fromkeys(uid.strip() for uid in data.examiner_ids if uid.strip()))
            examiners: list[User] = []
            for user_id in deduped:
                user = uow.users.get(user_id)
                if user.role != UserRole.EXAMINER:
                    raise DomainValidationError(
                        f"user {user.username} is not an EXAMINER account"
                    )
                if not user.is_active:
                    raise DomainValidationError(
                        f"examiner {user.username} is inactive and cannot be assigned"
                    )
                examiners.append(user)
            uow.users.assign_session_examiners(session.id, deduped, at)
            uow.audits.append(
                session.id,
                actor=data.actor,
                action="SESSION_EXAMINERS_UPDATED",
                target=session.id,
                details={
                    "examiner_ids": deduped,
                    "examiner_usernames": [user.username for user in examiners],
                },
            )
            uow.commit()
            return examiners


def ensure_bootstrap_user(
    uow_factory: UnitOfWorkFactory,
    *,
    username: str | None,
    password_hash: str | None,
    role: str,
) -> User | None:
    if not username or not password_hash:
        return None
    normalized_username = normalize_username(username)
    at = utc_now()
    with uow_factory() as uow:
        existing_any = uow.users.list_all()
        if existing_any:
            uow.commit()
            return None
        user = User(
            id=f"USR-{uuid4().hex[:10].upper()}",
            username=normalized_username,
            password_hash=password_hash,
            full_name="Quản trị hệ thống" if role == "ADMIN" else normalized_username,
            role=UserRole(role),
            is_active=True,
            created_at=at,
            updated_at=at,
        )
        uow.users.add(user)
        uow.commit()
        return user

