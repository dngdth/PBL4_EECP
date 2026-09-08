from dataclasses import dataclass, field
from typing import Any

from app.domain.entities.exam_session import PreflightCheck
from app.domain.value_objects.enums import Severity


@dataclass(frozen=True, slots=True)
class CreateSessionInput:
    exam_name: str
    room_id: str
    gateway_id: str
    workstation_ids: list[str]
    actor: str


@dataclass(frozen=True, slots=True)
class DeployPolicyInput:
    session_id: str
    profile: str
    rules: dict[str, Any]
    actor: str


@dataclass(frozen=True, slots=True)
class SubmitPreflightInput:
    session_id: str
    workstation_id: str
    checks: list[PreflightCheck]
    actor: str = "agent"


@dataclass(frozen=True, slots=True)
class StartSessionInput:
    session_id: str
    actor: str
    force: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TelemetryInput:
    session_id: str
    workstation_id: str
    event_type: str
    severity: Severity
    category: str
    action: str
    destination: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
