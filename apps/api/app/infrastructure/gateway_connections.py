from __future__ import annotations

import asyncio
from typing import Protocol


class GatewaySocket(Protocol):
    async def send_text(self, data: str) -> None: ...


class GatewayConnectionRegistry:
    """Ephemeral WebSocket state; never persisted in domain entities."""

    def __init__(self):
        self._connections: dict[str, GatewaySocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, gateway_id: str, socket: GatewaySocket) -> None:
        async with self._lock:
            self._connections[gateway_id] = socket

    async def disconnect(self, gateway_id: str, socket: GatewaySocket) -> None:
        async with self._lock:
            if self._connections.get(gateway_id) is socket:
                self._connections.pop(gateway_id, None)

    async def send(self, gateway_id: str, message: str) -> bool:
        async with self._lock:
            socket = self._connections.get(gateway_id)
        if socket is None:
            return False
        await socket.send_text(message)
        return True

    async def is_connected(self, gateway_id: str) -> bool:
        async with self._lock:
            return gateway_id in self._connections

    async def list_connected(self) -> tuple[str, ...]:
        async with self._lock:
            return tuple(sorted(self._connections))
