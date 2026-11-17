from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities.user import UserRole


class UserModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateUserRequest(UserModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=1024)
    full_name: str = Field(min_length=1, max_length=128)
    role: UserRole = UserRole.EXAMINER


class UpdateUserRequest(UserModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=6, max_length=1024)
    is_active: bool | None = None
    role: UserRole | None = None


class UserView(UserModel):
    id: str
    username: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime

