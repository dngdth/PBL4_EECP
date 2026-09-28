from __future__ import annotations

import hashlib
import json
import secrets
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from contracts.v2 import AgentHello, GatewayEnvelope, GatewayMessageType

router = APIRouter()


@router.websocket("/ws/agents")
async def agent_websocket(websocket: WebSocket) -> None:
    state = websocket.app.state
    supplied = websocket.headers.get("authorization", "")
    supplied_secret = supplied[7:] if supplied.startswith("Bearer ") else ""
    if not supplied_secret:
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
        if state.settings.agent_credentials_json:
            records = json.loads(state.settings.agent_credentials_json)
            record = records.get(hello.agent_id, {})
            actual = hashlib.sha256(supplied_secret.encode()).hexdigest()
            if record.get("revoked") is True:
                await state.router.report_security_audit(
                    hello.agent_id, "REVOKED_CREDENTIAL"
                )
                raise ValueError("Agent credential is revoked")
            if not secrets.compare_digest(actual, str(record.get("secret_sha256", ""))):
                await state.router.report_security_audit(
                    hello.agent_id, "INVALID_CREDENTIAL"
                )
                raise ValueError("Agent credential does not match identity")
        elif not secrets.compare_digest(
            supplied_secret, state.settings.agent_bootstrap_token
        ):
            await state.router.report_security_audit(
                hello.agent_id, "INVALID_CREDENTIAL"
            )
            raise ValueError("Agent credential is invalid")
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
                # Disconnect is already authoritative in the local registry. Presence is
                # best-effort and must not turn bounded uplink backpressure into an ASGI
                # teardown failure.
                with suppress(OSError):
                    await state.router.agent_disconnected(agent_id)
