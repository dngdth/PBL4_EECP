from __future__ import annotations

import asyncio
import secrets
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.application.security import Permission
from app.infrastructure.security import MachineCredentialRegistry
from app.presentation.api.deps import authorize
from contracts.v2 import GatewayEnvelope, GatewayMessageType
from contracts.v2.screen import (
    MAX_FRAME_BYTES,
    ScreenFrame,
    ScreenMode,
    ScreenRequest,
)

router = APIRouter(tags=["screen evidence"])


class ScreenRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: ScreenMode = ScreenMode.SNAPSHOT
    evidence_id: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    max_frames: int = Field(default=32, ge=1, le=32)
    max_bytes: int = Field(default=16 * 1024 * 1024, ge=1, le=16 * 1024 * 1024)


def session_for(request: Request, session_id: str, agent_id: str | None = None):
    with request.app.state.container.database.unit_of_work() as uow:
        session = uow.sessions.get(session_id)
        if agent_id is not None and agent_id not in session.workstations:
            raise HTTPException(403, "Agent is outside the requested exam session")
        return session


def audit(request: Request, session_id: str, action: str, target: str) -> None:
    principal = getattr(request.state, "principal", None)
    with request.app.state.container.database.unit_of_work() as uow:
        uow.audits.append(
            session_id,
            actor=principal.subject if principal else "development",
            action=action,
            target=target,
            details={"resource": "screen evidence"},
        )
        uow.commit()


def _audit_list_throttled(
    request: Request, session_id: str, target: str, *, interval_seconds: float = 60.0
) -> None:
    principal = getattr(request.state, "principal", None)
    actor = principal.subject if principal else "development"
    lock = getattr(request.app.state, "_screen_list_audit_lock", None)
    if lock is None:
        lock = threading.Lock()
        request.app.state._screen_list_audit_lock = lock
    recent: dict[tuple[str, str, str], float] = getattr(
        request.app.state, "_screen_list_audit_recent", {}
    )
    if not hasattr(request.app.state, "_screen_list_audit_recent"):
        request.app.state._screen_list_audit_recent = recent
    now = time.monotonic()
    key = (actor, session_id, target)
    with lock:
        previous = recent.get(key)
        if previous is not None and now - previous < interval_seconds:
            return
        if len(recent) >= 1024:
            cutoff = now - interval_seconds
            for stale_key in [k for k, seen in recent.items() if seen <= cutoff]:
                del recent[stale_key]
        recent[key] = now
    audit(request, session_id, "SCREEN_EVIDENCE_LISTED", target)


@router.get("/api/v1/sessions/{session_id}/screen/evidence")
def list_evidence(session_id: str, request: Request, agent_id: str | None = None) -> list:
    authorize(request, Permission.VIEW_INCIDENT, session_id=session_id)
    session = session_for(request, session_id, agent_id)
    _audit_list_throttled(request, session_id, agent_id or session_id)
    values = request.app.state.screen_store.list_evidence(session_id, agent_id)
    if values:
        by_id = {value["id"]: value for value in values}
        for value in values:
            value["event_ids"] = [value["event_id"]] if value.get("event_id") else []
        for event_id, workstation_id, evidence_id in (
            request.app.state.screen_store.linked_telemetry_events(session_id, agent_id)
        ):
            value = by_id.get(evidence_id)
            if (
                value is not None
                and value["agent_id"] == workstation_id
                and event_id not in value["event_ids"]
            ):
                value["event_ids"].append(event_id)
    if session.state.value != "RUNNING":
        for value in values:
            value["replay_available"] = False
    return values


@router.post("/api/v1/sessions/{session_id}/agents/{agent_id}/screen/requests", status_code=202)
async def request_screen(
    session_id: str,
    agent_id: str,
    payload: ScreenRequestInput,
    request: Request,
) -> dict:
    authorize(request, Permission.CONTROL_AGENT, session_id=session_id)
    session = session_for(request, session_id, agent_id)
    if session.state.value != "RUNNING":
        raise HTTPException(409, "session does not allow new screenshot capture requests")
    store = request.app.state.screen_store
    if payload.mode == ScreenMode.REPLAY:
        evidence = store.evidence(payload.evidence_id or "")
        if (
            evidence is None
            or evidence["session_id"] != session_id
            or (evidence["agent_id"] != agent_id)
        ):
            raise HTTPException(404, "evidence not found in this Agent session")
        if not evidence["replay_available"]:
            raise HTTPException(
                410, "client replay buffer has expired; stored images remain viewable"
            )
    now = datetime.now(UTC)
    try:
        value = ScreenRequest(
            protocol_version=2,
            request_id=uuid.uuid4().hex,
            agent_id=agent_id,
            session_id=session_id,
            mode=payload.mode,
            evidence_id=payload.evidence_id,
            issued_at=now,
            deadline=now + timedelta(seconds=60),
            start_at=payload.start_at,
            end_at=payload.end_at,
            max_frames=1 if payload.mode == ScreenMode.SNAPSHOT else payload.max_frames,
            max_bytes=payload.max_bytes,
        )
    except ValidationError as exc:
        raise HTTPException(422, "invalid screenshot request window or evidence ID") from exc
    # Avoid flooding the capture worker with duplicate examiner clicks.
    with store.connection() as connection:
        count = connection.execute(
            """SELECT COUNT(*) AS count FROM screen_requests WHERE agent_id=?
            AND deadline>? AND status IN ('capturing','uploading')""",
            (agent_id, now.isoformat()),
        ).fetchone()["count"]
        if count >= 4:
            raise HTTPException(429, "too many pending screenshot requests for this Agent")
    store.save_request(value)
    audit(request, session_id, "SCREEN_REQUESTED", value.request_id)
    binding = request.app.state.container.resolve_gateway_for_agent(agent_id)
    delivered = False
    if binding:
        envelope = GatewayEnvelope(
            protocol_version=2,
            message_type=GatewayMessageType.SCREEN_REQUEST,
            message_id=value.request_id,
            correlation_id=value.request_id,
            source_id="backend",
            target_id=agent_id,
            payload=value.model_dump(mode="json"),
        )
        try:
            delivered = await request.app.state.gateway_connections.send(
                binding.gateway_id, envelope.to_json()
            )
        except (OSError, RuntimeError):
            delivered = False
    if not delivered:
        from contracts.v2.screen import ScreenResponse

        store.save_response(
            ScreenResponse(
                protocol_version=2,
                request_id=value.request_id,
                agent_id=agent_id,
                session_id=session_id,
                evidence_id=value.evidence_id,
                status="unavailable",
                reason="Gateway is offline",
            )
        )
    return store.get_request(value.request_id)


@router.get("/api/v1/screen/requests/{request_id}")
def screen_request_status(request_id: str, request: Request) -> dict:
    value = request.app.state.screen_store.get_request(request_id)
    if value is None:
        raise HTTPException(404, "screenshot request not found")
    authorize(request, Permission.VIEW_INCIDENT, session_id=value["session_id"])
    return value


@router.get("/api/v1/screen/evidence/{evidence_id}/frames/{frame_id}")
def get_frame(evidence_id: str, frame_id: str, request: Request) -> Response:
    store = request.app.state.screen_store
    evidence = store.evidence(evidence_id)
    if evidence is None:
        raise HTTPException(404, "evidence not found")
    authorize(request, Permission.VIEW_INCIDENT, session_id=evidence["session_id"])
    result = store.read_frame(evidence_id, frame_id)
    if result is None:
        raise HTTPException(410, "screenshot expired or unavailable")
    frame, data = result
    audit(request, evidence["session_id"], "SCREEN_IMAGE_VIEWED", f"{evidence_id}:{frame_id}")
    return Response(
        data,
        media_type=frame.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-EECP-Captured-At": frame.captured_at.isoformat(),
            "X-EECP-SHA256": frame.sha256,
        },
    )


def authenticate_gateway(request: Request, gateway_id: str) -> None:
    settings = request.app.state.settings
    supplied = request.headers.get("authorization", "")
    secret = supplied[7:] if supplied.startswith("Bearer ") else ""
    if settings.gateway_credentials_json:
        valid = (
            MachineCredentialRegistry(settings.gateway_credentials_json).failure_reason(
                gateway_id, secret
            )
            is None
        )
    else:
        valid = bool(settings.gateway_bootstrap_token) and secrets.compare_digest(
            secret, settings.gateway_bootstrap_token
        )
    if not valid:
        raise HTTPException(401, "Gateway authentication required")


@router.post("/api/v2/screen/ingest/{gateway_id}")
async def ingest_frame(gateway_id: str, request: Request) -> dict:
    authenticate_gateway(request, gateway_id)
    header = request.headers.get("x-eecp-frame", "")
    if len(header) > 8192:
        raise HTTPException(413, "screenshot metadata too large")
    try:
        frame = ScreenFrame.model_validate_json(header)
    except ValidationError as exc:
        raise HTTPException(422, "invalid screenshot metadata") from exc
    binding = request.app.state.container.resolve_gateway_for_agent(frame.agent_id)
    if binding is None or binding.gateway_id != gateway_id:
        raise HTTPException(403, "Gateway is not bound to the screenshot Agent")
    session = session_for(request, frame.session_id, frame.agent_id)
    now = datetime.now(UTC)
    if (
        frame.captured_at < session.created_at - timedelta(seconds=5)
        or (frame.captured_at > now + timedelta(seconds=5))
        or (session.finished_at and frame.captured_at > session.finished_at + timedelta(seconds=15))
    ):
        raise HTTPException(409, "screenshot capture time is outside the session")
    store = request.app.state.screen_store
    if frame.request_id:
        authorized = store.get_request(frame.request_id)
        if (
            authorized is None
            or authorized["agent_id"] != frame.agent_id
            or (authorized["session_id"] != frame.session_id)
        ):
            raise HTTPException(403, "screenshot has no authorized capture request")
        control = ScreenRequest.model_validate(authorized["payload"])
        if control.mode == ScreenMode.SNAPSHOT and frame.kind != "snapshot":
            raise HTTPException(403, "snapshot request cannot upload unrelated context frames")
        response = authorized.get("response")
        if response is None:
            raise HTTPException(409, "capture manifest not yet synchronized; retry upload")
        expected = next(
            (value for value in response.get("frames", []) if value["frame_id"] == frame.frame_id),
            None,
        )
        if expected is None or ScreenFrame.model_validate(expected) != frame:
            raise HTTPException(403, "screenshot was not listed in the authorized manifest")
        if (
            len(response.get("frames", [])) > control.max_frames
            or sum(value["size_bytes"] for value in response.get("frames", [])) > control.max_bytes
        ):
            raise HTTPException(403, "screenshot manifest exceeds request budget")
        if control.evidence_id and control.evidence_id != frame.evidence_id:
            raise HTTPException(403, "screenshot does not match requested evidence")
        if frame.kind == "snapshot" and not (
            control.issued_at - timedelta(seconds=5) <= frame.captured_at <= control.deadline
        ):
            raise HTTPException(403, "snapshot was not captured during authorized request")
        if (control.start_at and frame.captured_at < control.start_at) or (
            control.end_at and frame.captured_at > control.end_at
        ):
            raise HTTPException(403, "screenshot is outside requested replay window")
    elif frame.kind not in {"trigger", "event_context"} or frame.event_id is None:
        raise HTTPException(403, "context images require an examiner replay request")
    else:
        existing = store.frames(frame.evidence_id)
        if any(
            value["kind"] == frame.kind and value["frame_id"] != frame.frame_id
            for value in existing
        ):
            raise HTTPException(409, "representative screenshot is already stored")
    if frame.event_id:
        with request.app.state.container.database.unit_of_work() as uow:
            event = uow.telemetry.find(frame.event_id)
            if event is None:
                raise HTTPException(409, "event is not yet synchronized; retry upload")
            if (
                event.session_id != frame.session_id
                or event.workstation_id != frame.agent_id
                or (event.payload.get("evidence_id") != frame.evidence_id)
                or frame.event_occurred_at != event.occurred_at
            ):
                raise HTTPException(403, "screenshot event identity mismatch")
    semaphore = request.app.state.screen_ingest_slots
    if semaphore.locked():
        raise HTTPException(429, "screenshot storage concurrency limit")
    async with semaphore:
        data = bytearray()
        try:
            async with asyncio.timeout(15):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > min(MAX_FRAME_BYTES, frame.size_bytes):
                        raise HTTPException(413, "screenshot exceeds byte limit")
                    data.extend(chunk)
        except TimeoutError as exc:
            raise HTTPException(408, "screenshot upload timed out") from exc
        try:
            receipt = await asyncio.to_thread(store.store_frame, frame, bytes(data))
        except ValueError as exc:
            raise HTTPException(409, "screenshot integrity/storage validation failed") from exc
        except OSError as exc:
            raise HTTPException(507, "screenshot storage unavailable or full") from exc
        realtime_hub = getattr(request.app.state, "realtime_hub", None)
        if realtime_hub is not None:
            await realtime_hub.publish_change(
                method="POST",
                path=f"/api/v1/sessions/{frame.session_id}/screen",
                session_id=frame.session_id,
                resource="sessions",
                resource_id=frame.session_id,
            )
        return receipt
