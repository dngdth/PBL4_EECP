from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.requests import Request
from starlette.responses import Response

from app.application.security import Principal, Role


class RealtimeHub:
    """In-process fan-out for dashboard invalidation events."""

    def __init__(self) -> None:
        self._connections: dict[WebSocket, tuple[asyncio.Lock, Principal | None]] = {}

    async def connect(
        self, websocket: WebSocket, *, principal: Principal | None = None
    ) -> None:
        await websocket.accept()
        self._connections[websocket] = (asyncio.Lock(), principal)
        await self.send(websocket, {"type": "connected", "occurred_at": _now()})

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.pop(websocket, None)

    async def send(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        entry = self._connections.get(websocket)
        if entry is None:
            return
        lock, _ = entry
        async with lock:
            await websocket.send_json(payload)

    async def publish_change(
        self,
        *,
        method: str,
        path: str,
        session_id: str | None = None,
        resource: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
    ) -> None:
        resolved_resource = resource or _resource_for(path)
        resolved_resource_id = resource_id or _resource_id_for(path)
        resolved_action = action or _action_for(method)
        target_session = session_id or (
            resolved_resource_id if resolved_resource == "sessions" else None
        )

        payload = {
            "type": "change",
            "resource": resolved_resource,
            "resource_id": resolved_resource_id,
            "action": resolved_action,
            "path": path,
            "occurred_at": _now(),
        }
        disconnected: list[WebSocket] = []
        for websocket, (_, principal) in tuple(self._connections.items()):
            if (
                principal is not None
                and principal.role != Role.ADMIN
                and target_session is not None
                and target_session not in principal.session_scope
            ):
                continue
            try:
                await asyncio.wait_for(self.send(websocket, payload), timeout=1)
            except (OSError, RuntimeError, TimeoutError, WebSocketDisconnect):
                disconnected.append(websocket)
        for websocket in disconnected:
            self.disconnect(websocket)


router = APIRouter(prefix="/api/v1", tags=["realtime"])


@router.websocket("/ws")
async def websocket_events(websocket: WebSocket) -> None:
    settings = getattr(websocket.app.state, "settings", None)
    principal: Principal | None = None
    if settings is not None and getattr(settings, "environment", "") == "production-like":
        token_service = getattr(websocket.app.state, "token_service", None)
        token = websocket.query_params.get("token")
        if not token:
            auth_header = websocket.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        if not token or token_service is None:
            await websocket.close(code=1008)
            return
        try:
            principal = token_service.verify(token)
        except ValueError:
            await websocket.close(code=1008)
            return

    hub: RealtimeHub = websocket.app.state.realtime_hub
    await hub.connect(websocket, principal=principal)
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
    if "/commands" in path:
        return "commands"
    if "/agents" in path:
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
