from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.requests import Request
from starlette.responses import Response


class RealtimeHub:
    """In-process fan-out for dashboard invalidation events."""

    def __init__(self) -> None:
        self._connections: dict[WebSocket, asyncio.Lock] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[websocket] = asyncio.Lock()
        await self.send(websocket, {"type": "connected", "occurred_at": _now()})

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.pop(websocket, None)

    async def send(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        lock = self._connections.get(websocket)
        if lock is None:
            return
        async with lock:
            await websocket.send_json(payload)

    async def publish_change(self, *, method: str, path: str) -> None:
        payload = {
            "type": "change",
            "resource": _resource_for(path),
            "resource_id": _resource_id_for(path),
            "action": _action_for(method),
            "path": path,
            "occurred_at": _now(),
        }
        disconnected: list[WebSocket] = []
        for websocket in tuple(self._connections):
            try:
                await asyncio.wait_for(self.send(websocket, payload), timeout=1)
            except (OSError, RuntimeError, TimeoutError, WebSocketDisconnect):
                disconnected.append(websocket)
        for websocket in disconnected:
            self.disconnect(websocket)


router = APIRouter(prefix="/api/v1", tags=["realtime"])


@router.websocket("/ws")
async def websocket_events(websocket: WebSocket) -> None:
    hub: RealtimeHub = websocket.app.state.realtime_hub
    await hub.connect(websocket)
    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                message = {}
            if message.get("type") == "ping":
                await hub.send(websocket, {"type": "pong", "occurred_at": _now()})
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(websocket)


async def realtime_notification_middleware(
    request: Request,
    call_next: Any,
) -> Response:
    response: Response = await call_next(request)
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.url.path.startswith("/api/v1/")
        and response.status_code < 400
    ):
        hub: RealtimeHub = request.app.state.realtime_hub
        await hub.publish_change(method=request.method, path=request.url.path)
    return response


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _resource_for(path: str) -> str:
    if "/policy-profiles" in path:
        return "policies"
    if "/commands/" in path:
        return "commands"
    if "/agents/" in path:
        return "agents"
    if "/sessions" in path:
        return "sessions"
    return "api"


def _resource_id_for(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    for collection in ("sessions", "agents", "commands", "policy-profiles"):
        if collection not in parts:
            continue
        index = parts.index(collection) + 1
        if index < len(parts) and parts[index] != "register":
            return parts[index]
    return None


def _action_for(method: str) -> str:
    return {
        "POST": "created",
        "PUT": "updated",
        "PATCH": "updated",
        "DELETE": "deleted",
    }.get(method, "changed")
