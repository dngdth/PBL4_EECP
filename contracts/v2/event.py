from enum import StrEnum

from pydantic import Field

from contracts.v2.common import JsonObject, OpaqueId, UtcDatetime, VersionedContract


class EventType(StrEnum):
    POLICY_VIOLATION = "POLICY_VIOLATION"
    FORBIDDEN_PROCESS_DETECTED = "FORBIDDEN_PROCESS_DETECTED"
    SERVICE_HEALTH = "SERVICE_HEALTH"
    DEVICE_EVENT = "DEVICE_EVENT"
    POLICY_INTEGRITY = "POLICY_INTEGRITY"
    NETWORK_ATTEMPT = "NETWORK_ATTEMPT"


class Event(VersionedContract):
    event_id: OpaqueId
    session_id: OpaqueId
    agent_id: OpaqueId
    event_type: EventType
    occurred_at: UtcDatetime
    sequence: int | None = Field(default=None, strict=True, ge=0)
    payload: JsonObject = Field(default_factory=dict)
    correlation_id: OpaqueId | None = None
