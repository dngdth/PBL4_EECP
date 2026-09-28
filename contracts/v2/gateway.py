from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from contracts.v2.common import JsonObject, OpaqueId, UtcDatetime, VersionedContract
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


class AgentHello(VersionedContract):
    agent_id: OpaqueId
    hostname: OpaqueId
    ip_address: OpaqueId
    agent_version: OpaqueId


class DeliveryFailure(VersionedContract):
    command_id: OpaqueId
    agent_id: OpaqueId
    reason: OpaqueId
