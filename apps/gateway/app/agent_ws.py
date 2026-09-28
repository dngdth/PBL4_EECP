from __future__ import annotations

import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from contracts.v2 import AgentHello, GatewayEnvelope, GatewayMessageType

router = APIRouter()


@router.websocket("/ws/agents")
async def agent_websocket(websocket: WebSocket) -> None:
    state = websocket.app.state
    expected = f"Bearer {state.settings.agent_bootstrap_token}"
    supplied = websocket.headers.get("authorization", "")
    if not secrets.compare_digest(supplied, expected):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    agent_id: str | None = None
    try:
        first = GatewayEnvelope.model_validate_json(await websocket.receive_text())
        if first.message_type != GatewayMessageType.AGENT_HELLO:
            raise ValueError("first Agent message must be AGENT_HELLO")
        hello = AgentHello.model_validate(first.payload)
        if first.source_id != hello.agent_id:
            raise ValueError("Agent hello identity mismatch")
        agent_id = hello.agent_id
        await state.connections.connect(agent_id, websocket)
        await state.router.agent_connected(hello)

        while True:
            envelope = GatewayEnvelope.model_validate_json(await websocket.receive_text())
            await state.router.route_agent(agent_id, envelope)
    except (ValidationError, ValueError):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    except WebSocketDisconnect:
        pass
    finally:
        if agent_id is not None:
            removed = await state.connections.disconnect(agent_id, websocket)
            if removed:
                await state.router.agent_disconnected(agent_id)
