from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.requests import Request
from starlette.responses import Response

from app.application.security import Principal, Role
from app.presentation.api.deps import auth_enforced, hydrate_principal


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

    def update_principal(
        self, websocket: WebSocket, principal: Principal | None
    ) -> None:
        entry = self._connections.get(websocket)
        if entry is not None:
            lock, _ = entry
            self._connections[websocket] = (lock, principal)

    async def _revalidate_connection(
        self,
        websocket: WebSocket,
        lock: asyncio.Lock,
        principal: Principal | None,
        app: Any,
    ) -> tuple[bool, Principal | None]:
        if principal is None or app is None:
            return True, principal
        settings = getattr(getattr(app, "state", None), "settings", None)
        if settings is None or not auth_enforced(settings):
            return True, principal
        try:
            refreshed = hydrate_principal(app, principal)
            if refreshed.role != principal.role:
                raise ValueError("account role changed")
        except ValueError:
            with contextlib.suppress(
                OSError, RuntimeError, TimeoutError, WebSocketDisconnect
            ):
                await asyncio.wait_for(websocket.close(code=1008), timeout=1)
            return False, None
        self._connections[websocket] = (lock, refreshed)
        return True, refreshed

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
        app: Any = None,
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
        for websocket, (lock, principal) in tuple(self._connections.items()):
            alive, principal = await self._revalidate_connection(
                websocket,
                lock,
                principal,
                app or getattr(websocket, "app", None),
            )
            if not alive:
                disconnected.append(websocket)
                continue
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
    if settings is not None and auth_enforced(settings):
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
            verified = token_service.verify(token)
            principal = hydrate_principal(websocket.app, verified)
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
                if settings is not None and auth_enforced(settings) and principal is not None:
                    try:
                        refreshed = hydrate_principal(websocket.app, principal)
                        if refreshed.role != principal.role:
                            raise ValueError("account role changed")
                    except ValueError:
                        await websocket.close(code=1008)
                        return
                    principal = refreshed
                    hub.update_principal(websocket, refreshed)
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
        await hub.publish_change(
            method=request.method,
            path=request.url.path,
            app=request.app,
        )
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
