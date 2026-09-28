from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.exceptions.errors import PolicyValidationError


class GatewayStatus(StrEnum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


@dataclass(slots=True)
class Gateway:
    id: str
    room_id: str
    status: GatewayStatus
    version: str
    last_seen: datetime
    created_at: datetime
    connected_agent_count: int = 0
    backend_uplink_status: GatewayStatus = GatewayStatus.OFFLINE

    @classmethod
    def register(
        cls, gateway_id: str, room_id: str, version: str, at: datetime
    ) -> Gateway:
        return cls(
            id=_required(gateway_id, "gateway id"),
            room_id=_required(room_id, "room id"),
            status=GatewayStatus.ONLINE,
            version=_required(version, "gateway version"),
            last_seen=at,
            created_at=at,
        )

    def reregister(self, room_id: str, version: str, at: datetime) -> None:
        self.room_id = _required(room_id, "room id")
        self.version = _required(version, "gateway version")
        self.heartbeat(at)

    def heartbeat(self, at: datetime) -> None:
        self.status = GatewayStatus.ONLINE
        self.last_seen = at

    def update_health(
        self,
        at: datetime,
        connected_agent_count: int,
        backend_uplink_status: GatewayStatus,
    ) -> None:
        if connected_agent_count < 0:
            raise PolicyValidationError("connected agent count must not be negative")
        self.heartbeat(at)
        self.connected_agent_count = connected_agent_count
        self.backend_uplink_status = backend_uplink_status

    def disconnect(self, at: datetime) -> None:
        self.status = GatewayStatus.OFFLINE
        self.backend_uplink_status = GatewayStatus.OFFLINE
        self.last_seen = at


@dataclass(frozen=True, slots=True)
class AgentGatewayBinding:
    agent_id: str
    gateway_id: str
    bound_at: datetime

    def __post_init__(self) -> None:
        _required(self.agent_id, "agent id")
        _required(self.gateway_id, "gateway id")


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise PolicyValidationError(f"{name} must not be empty")
    return normalized
