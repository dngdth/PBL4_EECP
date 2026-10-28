from __future__ import annotations

import asyncio

from contracts.v2 import Presence


class PresenceStore:
    """Ephemeral local presence; this is not an incident or durable business store."""

    def __init__(self):
        self._values: dict[str, Presence] = {}
        self._lock = asyncio.Lock()

    async def put(self, presence: Presence) -> None:
        async with self._lock:
            self._values[presence.agent_id] = presence

    async def get(self, agent_id: str) -> Presence | None:
        async with self._lock:
            return self._values.get(agent_id)

    async def list_all(self) -> tuple[Presence, ...]:
        async with self._lock:
            return tuple(self._values[key] for key in sorted(self._values))
