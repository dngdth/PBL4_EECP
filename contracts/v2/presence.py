from enum import StrEnum

from contracts.v2.common import OpaqueId, Sha256Hex, UtcDatetime, VersionedContract


class PresenceHealth(StrEnum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


class ServiceHealth(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class Presence(VersionedContract):
    agent_id: OpaqueId
    gateway_id: OpaqueId | None = None
    last_seen: UtcDatetime
    health: PresenceHealth
    active_policy_hash: Sha256Hex | None = None
    service_health: ServiceHealth | None = None
    agent_version: OpaqueId | None = None
