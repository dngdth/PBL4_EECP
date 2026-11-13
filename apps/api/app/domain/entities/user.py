from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.exceptions.errors import DomainValidationError


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    EXAMINER = "EXAMINER"


def normalize_username(username: str) -> str:
    cleaned = username.strip().lower()
    if len(cleaned) < 2 or len(cleaned) > 64:
        raise DomainValidationError("username must be between 2 and 64 characters")
    if any(ch.isspace() for ch in cleaned):
        raise DomainValidationError("username must not contain whitespace")
    return cleaned


def normalize_full_name(full_name: str) -> str:
    cleaned = full_name.strip()
    if not cleaned or len(cleaned) > 128:
        raise DomainValidationError("full_name must be between 1 and 128 characters")
    return cleaned


@dataclass(frozen=True, slots=True)
class User:
    id: str
    username: str
    password_hash: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime

