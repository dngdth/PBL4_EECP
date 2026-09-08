from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects.enums import CommandStatus, CommandType


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcknowledgeCommandRequest(ApiModel):
    success: bool
    policy_hash: str | None = None
    error: str | None = None
    actor: str = Field(default="agent", min_length=1)


class CommandView(ApiModel):
    id: str
    session_id: str
    target_id: str
    type: CommandType
    payload: dict[str, Any]
    status: CommandStatus
    created_at: datetime
    attempt_count: int
    last_attempt_at: datetime | None
    next_retry_at: datetime | None
    expires_at: datetime


class PolicyProfileView(ApiModel):
    id: str
    label: str
    description: str
    rules: dict[str, Any]
    is_builtin: bool
    yaml: str


class CreatePolicyProfileRequest(ApiModel):
    id: str = Field(min_length=2, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    rules: dict[str, Any]


class UpdatePolicyProfileRequest(ApiModel):
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    rules: dict[str, Any]
