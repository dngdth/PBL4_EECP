from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from contracts.v2.common import JsonObject, OpaqueId, UtcDatetime, VersionedContract
from contracts.v2.errors import ErrorCode
from contracts.v2.presence import PresenceHealth


class GatewayMessageType(StrEnum):
    GATEWAY_HELLO = "GATEWAY_HELLO"
    GATEWAY_HEALTH = "GATEWAY_HEALTH"
    AGENT_HELLO = "AGENT_HELLO"
    PRESENCE = "PRESENCE"
    COMMAND = "COMMAND"
    ACK = "ACK"
    EVENT = "EVENT"
    DELIVERY_FAILURE = "DELIVERY_FAILURE"
    ERROR = "ERROR"
    EVENT_RECEIPT = "EVENT_RECEIPT"
    SECURITY_AUDIT = "SECURITY_AUDIT"
    AGENT_SESSION_STATE = "AGENT_SESSION_STATE"


class EventReceiptStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    REJECTED = "REJECTED"


class AgentSessionStateStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MISMATCH = "MISMATCH"


class GatewayEnvelope(VersionedContract):
    message_type: GatewayMessageType
    message_id: OpaqueId
    correlation_id: OpaqueId
    source_id: OpaqueId
    target_id: OpaqueId | None = None
    payload: JsonObject = Field(default_factory=dict)


class GatewayHello(VersionedContract):
    gateway_id: OpaqueId
    room_id: OpaqueId
    gateway_version: OpaqueId


class GatewayHealth(VersionedContract):
    gateway_id: OpaqueId
    room_id: OpaqueId
    status: PresenceHealth
    last_seen: UtcDatetime
    connected_agent_count: int = Field(strict=True, ge=0)
    backend_uplink_status: PresenceHealth
    pending_event_count: int | None = Field(default=None, strict=True, ge=0)
    oldest_pending_event_age: float | None = Field(default=None, ge=0)
    last_flush_success_at: UtcDatetime | None = None
    last_flush_error: str | None = None
    buffer_status: PresenceHealth | None = None
    outbound_queue_depth: int | None = Field(default=None, strict=True, ge=0)
    outbound_queue_capacity: int | None = Field(default=None, strict=True, ge=1)
    outbound_queue_saturation_count: int | None = Field(default=None, strict=True, ge=0)


class AgentHello(VersionedContract):
    agent_id: OpaqueId
    hostname: OpaqueId
    ip_address: OpaqueId
    agent_version: OpaqueId


class AgentSessionState(VersionedContract):
    """Backend-authoritative session state sent after an Agent reconnects."""

    agent_id: OpaqueId
    status: AgentSessionStateStatus
    session_id: OpaqueId | None = None
    policy_hash: OpaqueId | None = None
    policy: JsonObject | None = None
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_state(self) -> AgentSessionState:
        values = (self.session_id, self.policy_hash, self.policy)
        if self.status == AgentSessionStateStatus.ACTIVE and any(
            value is None for value in values
        ):
            raise ValueError("active Agent session state requires session and policy")
        if (
            self.status == AgentSessionStateStatus.ACTIVE
            and self.policy is not None
            and self.policy.get("policy_hash") != self.policy_hash
        ):
            raise ValueError("Agent session policy hash does not match state")
        if self.status != AgentSessionStateStatus.ACTIVE and any(
            value is not None for value in values
        ):
            raise ValueError("inactive Agent session state cannot include policy")
        return self


class DeliveryFailure(VersionedContract):
    command_id: OpaqueId
    agent_id: OpaqueId
    reason: OpaqueId


class EventReceipt(VersionedContract):
    event_id: OpaqueId
    status: EventReceiptStatus
    received_at: UtcDatetime
    error_code: ErrorCode | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_receipt(self) -> EventReceipt:
        if self.status == EventReceiptStatus.REJECTED and self.error_code is None:
            raise ValueError("rejected Event receipt requires error_code")
        if self.status != EventReceiptStatus.REJECTED and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError("successful Event receipt cannot contain an error")
        return self
