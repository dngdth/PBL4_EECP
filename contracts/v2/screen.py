"""Bounded screenshot control and metadata; image bytes use a separate HTTP data plane."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.v2.common import OpaqueId, Sha256Hex, UtcDatetime, VersionedContract

ScreenId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,160}$")]
MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_EVENT_CONTEXT_SECONDS = 30


class ScreenMode(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    REPLAY = "REPLAY"


class ScreenStatus(StrEnum):
    CAPTURING = "capturing"
    READY = "ready"
    AVAILABLE = "available"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


class ScreenSessionState(VersionedContract):
    agent_id: OpaqueId
    session_id: OpaqueId | None = None
    observed_at: UtcDatetime


class ScreenRequest(VersionedContract):
    request_id: ScreenId
    agent_id: OpaqueId
    session_id: OpaqueId
    mode: ScreenMode
    evidence_id: ScreenId | None = None
    issued_at: UtcDatetime
    deadline: UtcDatetime
    start_at: UtcDatetime | None = None
    end_at: UtcDatetime | None = None
    max_frames: int = Field(default=32, strict=True, ge=1, le=32)
    max_bytes: int = Field(default=MAX_REQUEST_BYTES, strict=True, ge=1, le=MAX_REQUEST_BYTES)

    @model_validator(mode="after")
    def validate_window(self) -> ScreenRequest:
        if self.deadline <= self.issued_at:
            raise ValueError("screen deadline must follow issue time")
        if (self.deadline - self.issued_at).total_seconds() > 120:
            raise ValueError("screen request lifetime exceeds 120 seconds")
        if self.mode == ScreenMode.REPLAY and self.evidence_id is None:
            raise ValueError("replay requires evidence_id")
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValueError("invalid replay window")
        return self


class ScreenFrame(VersionedContract):
    frame_id: ScreenId
    evidence_id: ScreenId
    agent_id: OpaqueId
    session_id: OpaqueId
    event_id: OpaqueId | None = None
    request_id: ScreenId | None = None
    captured_at: UtcDatetime
    event_occurred_at: UtcDatetime | None = None
    event_received_at: UtcDatetime | None = None
    available_until: UtcDatetime
    policy_hash: Sha256Hex | None = None
    monitor_id: OpaqueId = "virtual-desktop"
    kind: Literal["context", "event_context", "trigger", "snapshot"]
    width: int = Field(strict=True, ge=1, le=16384)
    height: int = Field(strict=True, ge=1, le=16384)
    content_type: Literal["image/jpeg", "image/webp"] = "image/jpeg"
    size_bytes: int = Field(strict=True, ge=1, le=MAX_FRAME_BYTES)
    sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_capture(self) -> ScreenFrame:
        if self.available_until <= self.captured_at:
            raise ValueError("frame retention must follow capture time")
        if self.width * self.height > 40_000_000:
            raise ValueError("frame pixel limit exceeded")
        if self.kind == "event_context":
            if self.event_id is None or self.event_occurred_at is None:
                raise ValueError("event context requires an event identity and occurrence time")
            age = (self.event_occurred_at - self.captured_at).total_seconds()
            if not 0 <= age <= MAX_EVENT_CONTEXT_SECONDS:
                raise ValueError("event context must precede the event by at most 30 seconds")
        return self


class ScreenResponse(VersionedContract):
    request_id: ScreenId
    agent_id: OpaqueId
    session_id: OpaqueId
    evidence_id: ScreenId | None = None
    event_id: OpaqueId | None = None
    status: ScreenStatus
    available_until: UtcDatetime | None = None
    frames: list[ScreenFrame] = Field(default_factory=list, max_length=32)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_frames(self) -> ScreenResponse:
        for frame in self.frames:
            if frame.agent_id != self.agent_id or frame.session_id != self.session_id:
                raise ValueError("response frame identity mismatch")
            if self.evidence_id is not None and frame.evidence_id != self.evidence_id:
                raise ValueError("response evidence identity mismatch")
        if sum(frame.size_bytes for frame in self.frames) > MAX_REQUEST_BYTES:
            raise ValueError("response exceeds byte limit")
        return self
