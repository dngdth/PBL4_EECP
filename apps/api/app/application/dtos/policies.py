from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AcknowledgeCommandInput:
    command_id: str
    success: bool
    policy_hash: str | None = None
    error: str | None = None
    actor: str = "agent"


@dataclass(frozen=True, slots=True)
class PolicyProfileDetails:
    id: str
    label: str
    description: str
    rules: dict[str, Any]
    is_builtin: bool


@dataclass(frozen=True, slots=True)
class CreatePolicyProfileInput:
    id: str
    label: str
    description: str
    rules: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UpdatePolicyProfileInput:
    id: str
    label: str
    description: str
    rules: dict[str, Any]
