from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


class PresenceStore(Protocol):
    def set_agent(self, agent_id: str, values: dict[str, Any]) -> None: ...
    def set_gateway(self, gateway_id: str, values: dict[str, Any]) -> None: ...
    def get_agent(self, agent_id: str) -> dict[str, Any] | None: ...
    def get_gateway(self, gateway_id: str) -> dict[str, Any] | None: ...
    def health(self) -> bool: ...


@dataclass(slots=True)
class MemoryPresenceStore:
    ttl_seconds: int
    _values: dict[str, tuple[datetime, dict[str, Any]]] = field(
        init=False, default_factory=dict
    )

    def _set(self, key: str, values: dict[str, Any]) -> None:
        self._values[key] = (datetime.now(UTC), dict(values))

    def _get(self, key: str) -> dict[str, Any] | None:
        item = self._values.get(key)
        if item is None:
            return None
        created, values = item
        if (datetime.now(UTC) - created).total_seconds() >= self.ttl_seconds:
            self._values.pop(key, None)
            return None
        return dict(values)

    def set_agent(self, agent_id: str, values: dict[str, Any]) -> None:
        self._set(f"presence:agent:{agent_id}", values)

    def set_gateway(self, gateway_id: str, values: dict[str, Any]) -> None:
        self._set(f"presence:gateway:{gateway_id}", values)

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        return self._get(f"presence:agent:{agent_id}")

    def get_gateway(self, gateway_id: str) -> dict[str, Any] | None:
        return self._get(f"presence:gateway:{gateway_id}")

    def health(self) -> bool:
        return True


class RedisPresenceStore:
    """TTL-only operational state. Never a business source of truth."""

    def __init__(self, redis_url: str, ttl_seconds: int):
        try:
            from redis import Redis
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("Redis presence requires the redis package") from exc
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds

    def _set(self, key: str, values: dict[str, Any]) -> None:
        payload = {**values, "refreshed_at": datetime.now(UTC).isoformat()}
        self._redis.set(key, json.dumps(payload, separators=(",", ":")), ex=self._ttl)
        self._redis.publish("presence:changed", json.dumps({"key": key, **payload}))

    def _get(self, key: str) -> dict[str, Any] | None:
        raw = self._redis.get(key)
        return json.loads(raw) if raw else None

    def set_agent(self, agent_id: str, values: dict[str, Any]) -> None:
        self._set(f"presence:agent:{agent_id}", values)

    def set_gateway(self, gateway_id: str, values: dict[str, Any]) -> None:
        self._set(f"presence:gateway:{gateway_id}", values)

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        return self._get(f"presence:agent:{agent_id}")

    def get_gateway(self, gateway_id: str) -> dict[str, Any] | None:
        return self._get(f"presence:gateway:{gateway_id}")

    def health(self) -> bool:
        try:
            return bool(self._redis.ping())
        except Exception:
            return False
