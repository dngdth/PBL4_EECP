from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.domain.value_objects.enums import CommandStatus, CommandType, IncidentStatus, Severity
from app.domain.value_objects.primitives import new_id, utc_now

COMMAND_RETRY_DELAY = timedelta(seconds=10)
COMMAND_TTL = timedelta(minutes=1)
MAX_COMMAND_ATTEMPTS = 3


@dataclass(slots=True)
class Command:
    session_id: str
    target_id: str
    type: CommandType
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: new_id("cmd"))
    status: CommandStatus = CommandStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    acknowledged_at: datetime | None = None
    error: str | None = None
    attempt_count: int = 0
    last_attempt_at: datetime | None = None
    next_retry_at: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.expires_at is None:
            self.expires_at = self.created_at + COMMAND_TTL

    def deliver(self, at: datetime) -> None:
        if self.status not in {CommandStatus.PENDING, CommandStatus.DELIVERED}:
            raise ValueError(f"cannot deliver command while it is {self.status}")
        if self.should_timeout(at):
            raise ValueError("expired command cannot be delivered")
        self.status = CommandStatus.DELIVERED
        self.attempt_count += 1
        self.last_attempt_at = at
        self.next_retry_at = at + COMMAND_RETRY_DELAY

    def should_timeout(self, at: datetime) -> bool:
        return self.is_expired(at) or self.attempt_count >= MAX_COMMAND_ATTEMPTS

    def is_expired(self, at: datetime) -> bool:
        return self.expires_at is not None and at >= self.expires_at

    def time_out(self) -> None:
        if self.status not in {CommandStatus.PENDING, CommandStatus.DELIVERED}:
            raise ValueError(f"cannot time out command while it is {self.status}")
        self.status = CommandStatus.TIMED_OUT
        self.error = "command acknowledgement timed out"
        self.next_retry_at = None


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    session_id: str
    workstation_id: str
    event_type: str
    severity: Severity
    category: str
    action: str
    destination: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("evt"))
    occurred_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Incident:
    session_id: str
    category: str
    severity: Severity
    evidence: dict[str, Any]
    workstation_id: str | None = None
    id: str = field(default_factory=lambda: new_id("inc"))
    status: IncidentStatus = IncidentStatus.OPEN
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    session_id: str
    actor: str
    action: str
    target: str
    details: dict[str, Any]
    previous_hash: str
    chain_hash: str
    id: str = field(default_factory=lambda: new_id("aud"))
    occurred_at: datetime = field(default_factory=utc_now)
