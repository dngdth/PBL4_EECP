from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol


class AgentSocket(Protocol):
    async def send_text(self, data: str) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class AgentConnectionManager:
    """Concurrency-safe agent socket registry; newest connection wins."""

    def __init__(self):
        self._connections: dict[str, AgentSocket] = {}
        self._last_seen: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def connect(self, agent_id: str, socket: AgentSocket) -> None:
        async with self._lock:
            previous = self._connections.get(agent_id)
            self._connections[agent_id] = socket
            self._last_seen[agent_id] = datetime.now(UTC)
        if previous is not None and previous is not socket:
            await previous.close(code=4001, reason="replaced by newer connection")

    async def disconnect(self, agent_id: str, socket: AgentSocket) -> bool:
        async with self._lock:
            if self._connections.get(agent_id) is not socket:
                return False
            self._connections.pop(agent_id, None)
            self._last_seen[agent_id] = datetime.now(UTC)
            return True

    async def touch(self, agent_id: str) -> None:
        async with self._lock:
            if agent_id in self._connections:
                self._last_seen[agent_id] = datetime.now(UTC)

    async def send(self, agent_id: str, message: str) -> bool:
        async with self._lock:
            socket = self._connections.get(agent_id)
        if socket is None:
            return False
        await socket.send_text(message)
        return True

    async def is_connected(self, agent_id: str) -> bool:
        async with self._lock:
            return agent_id in self._connections

    async def list_connected(self) -> tuple[str, ...]:
        async with self._lock:
            return tuple(sorted(self._connections))

    async def last_seen(self, agent_id: str) -> datetime | None:
        async with self._lock:
            return self._last_seen.get(agent_id)
