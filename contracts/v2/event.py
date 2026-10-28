from __future__ import annotations

import json
from enum import StrEnum

from pydantic import Field, model_validator

from contracts.v2.common import JsonObject, OpaqueId, UtcDatetime, VersionedContract


class EventType(StrEnum):
    POLICY_VIOLATION = "POLICY_VIOLATION"
    USER_SESSION_CHANGED = "USER_SESSION_CHANGED"
    FORBIDDEN_PROCESS_DETECTED = "FORBIDDEN_PROCESS_DETECTED"
    ENFORCEMENT_ACTION = "ENFORCEMENT_ACTION"
    ENFORCEMENT_TAMPER = "ENFORCEMENT_TAMPER"
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

    @model_validator(mode="after")
    def validate_payload_size(self) -> Event:
        encoded = json.dumps(self.payload, separators=(",", ":")).encode()
        if len(encoded) > 64 * 1024:
            raise ValueError("event payload exceeds 64 KiB")
        return self
