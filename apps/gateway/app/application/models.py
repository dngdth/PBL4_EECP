from dataclasses import dataclass
from datetime import datetime

from contracts.v2 import GatewayEnvelope, PresenceHealth


@dataclass(frozen=True, slots=True)
class BufferedEvent:
    event_id: str
    envelope: GatewayEnvelope
    state: str
    attempt_count: int
    next_attempt_at: datetime
    last_error: str | None


@dataclass(frozen=True, slots=True)
class EventBufferHealth:
    pending_event_count: int
    oldest_pending_event_age: float | None
    last_flush_success_at: datetime | None
    last_flush_error: str | None
    buffer_status: PresenceHealth
    terminal_failed_count: int
