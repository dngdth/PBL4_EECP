from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agent.client.screen.buffer import RingBuffer
from agent.client.screen.capture import CapturedFrame, ScreenSettings, WindowsScreenCapture
from contracts.v2 import Event
from contracts.v2.screen import (
    AUTOMATIC_FRAME_LIMITS,
    MAX_EVENT_CONTEXT_SECONDS,
    ScreenFrame,
    ScreenMode,
    ScreenRequest,
    ScreenResponse,
    ScreenStatus,
)


@dataclass
class Evidence:
    evidence_id: str
    session_id: str
    event_id: str | None
    occurred_at: datetime
    created_at: datetime
    expires_at: datetime
    capture_until: datetime
    merge_key: tuple
    policy_hash: str | None
    frames: list[tuple[CapturedFrame, str]] = field(default_factory=list)
    needs_trigger: bool = True
    reason: str | None = None
    stored: set[str] = field(default_factory=set)
    event_ids: set[str] = field(default_factory=set)
    capture_stopped: bool = False


@dataclass
class UploadJob:
    metadata: ScreenFrame
    frame: CapturedFrame
    attempts: int = 0
    next_attempt: float = 0.0


def _format_capture_error(exc: Exception) -> str:
    text = str(exc)
    text_lower = text.lower()
    if "locked" in text_lower or "secure" in text_lower:
        return f"desktop locked or secure (Win+L / UAC): {text}"[:500]
    return text[:500]


class EvidenceManager:
    """Bounded RAM ownership with independent capture and upload workers.

    Immutable compressed frame objects are shared by ring/evidence/jobs, not copied.
    Four pre-event representatives and one trigger upload automatically. Full context
    uploads require a scoped request. Stopped sessions retain only queued frames.
    """

    def __init__(
        self,
        agent_id: str,
        send_response: Callable[[ScreenResponse], None],
        upload: Callable[[ScreenFrame, bytes], bool],
        *,
        settings: ScreenSettings | None = None,
        capture: Callable[[bool], CapturedFrame] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        log: Callable[[str], None] = print,
        require_session_authorization: bool = False,
    ):
        self.agent_id = agent_id
        self.settings = settings or ScreenSettings.from_env()
        self.ring = RingBuffer(
            self.settings.pre_seconds,
            self.settings.ring_max_frames,
            self.settings.ring_max_bytes,
        )
        self._capture = capture or WindowsScreenCapture(
            low_width=int(os.getenv("EECP_SCREEN_LOW_WIDTH", "960")),
            high_width=int(os.getenv("EECP_SCREEN_HIGH_WIDTH", "2560")),
        )
        self._send = send_response
        self._upload = upload
        self._clock = clock
        self._log = log
        self._lock = threading.RLock()
        self._session_id: str | None = None
        self._capture_epoch = 0
        self._require_session_authorization = require_session_authorization
        self._authorized_session_id: str | None = None
        self._policy_hash: str | None = None
        self._items: OrderedDict[str, Evidence] = OrderedDict()
        self._jobs: OrderedDict[tuple[str, str, str | None], UploadJob] = OrderedDict()
        self._requests: queue.Queue[ScreenRequest] = queue.Queue(maxsize=16)
        self._responses: OrderedDict[str, ScreenResponse] = OrderedDict()
        self._response_expiry: dict[str, datetime] = {}
        self._notice_cache: dict[str, tuple[ScreenStatus, str | None, float]] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._next_capture = 0.0
        self._last_notice = 0.0
        self.last_error: str | None = None

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for target, name in ((self._capture_loop, "capture"), (self._upload_loop, "upload")):
            thread = threading.Thread(target=target, name=f"eecp-screen-{name}", daemon=True)
            self._threads.append(thread)
            thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in self._threads:
            thread.join(timeout=12)
        self._threads.clear()
        self.deactivate()
        with self._lock:
            self._clear()

    def activate(self, session_id: str, policy: dict) -> None:
        with self._lock:
            if self._require_session_authorization and self._authorized_session_id != session_id:
                self.deactivate()
                return
            if self._session_id != session_id:
                self.deactivate()
                self._capture_epoch += 1
                self._next_capture = 0
            self._session_id = session_id
            self._policy_hash = policy.get("policy_hash")

    def authorize_session(self, session_id: str | None) -> None:
        with self._lock:
            self._authorized_session_id = session_id
            if self._session_id != session_id:
                self.deactivate()

    def deactivate(self) -> None:
        with self._lock:
            previous_session = self._session_id
            self._session_id = None
            self._policy_hash = None
            self._capture_epoch += 1
            self.ring.clear()
            now = self._clock()
            for evidence_id, item in tuple(self._items.items()):
                if item.session_id != previous_session or item.capture_stopped:
                    continue
                item.capture_stopped = True
                item.needs_trigger = False
                item.capture_until = min(item.capture_until, now)
                pending = {key[1] for key in self._jobs if key[0] == evidence_id}
                item.frames[:] = [pair for pair in item.frames if pair[0].frame_id in pending]
                if not pending and not item.stored:
                    item.reason = "session ended before an image could be queued for upload"
                self._notice(item, self._status(item))
                if not pending:
                    self._release(evidence_id)
            while not self._requests.empty():
                try:
                    request = self._requests.get_nowait()
                except queue.Empty:
                    break
                self._respond(request, ScreenStatus.UNAVAILABLE, reason="capture session ended")
            self.prune(now)
            self._wake.set()

    def _release(self, evidence_id: str) -> None:
        self._items.pop(evidence_id, None)
        self._notice_cache.pop(evidence_id, None)
        for request_id, response in tuple(self._responses.items()):
            if response.evidence_id == evidence_id:
                self._responses.pop(request_id, None)
                self._response_expiry.pop(request_id, None)

    def _clear(self) -> None:
        self.ring.clear()
        self._items.clear()
        self._jobs.clear()
        self._responses.clear()
        self._response_expiry.clear()
        self._notice_cache.clear()
        while not self._requests.empty():
            try:
                self._requests.get_nowait()
            except queue.Empty:
                break

    @property
    def evidence_bytes(self) -> int:
        frames = {
            frame.frame_id: frame for item in self._items.values() for frame, _kind in item.frames
        }
        return sum(len(frame.data) for frame in frames.values())

    def on_event(self, event: Event) -> str | None:
        if (
            event.event_type.value
            not in {
                "POLICY_VIOLATION",
                "FORBIDDEN_PROCESS_DETECTED",
                "ENFORCEMENT_ACTION",
                "ENFORCEMENT_TAMPER",
                "POLICY_INTEGRITY",
                "NETWORK_ATTEMPT",
                "DEVICE_EVENT",
            }
            or event.payload.get("severity") == "INFO"
        ):
            return None
        with self._lock:
            now = self._clock()
            self.prune(now)
            if event.agent_id != self.agent_id or event.session_id != self._session_id:
                return None
            key = (
                event.event_type,
                *(
                    str(event.payload.get(k, ""))
                    for k in (
                        "category",
                        "destination",
                        "process_name",
                        "action",
                        "severity",
                        "source",
                        "pid",
                        "process_creation_time",
                        "username",
                        "target",
                        "device_id",
                    )
                ),
            )
            process_event = event.event_type.value in {
                "FORBIDDEN_PROCESS_DETECTED", "ENFORCEMENT_ACTION"
            } or bool(event.payload.get("process_name"))
            can_merge = (
                bool(event.payload.get("pid") and event.payload.get("process_creation_time"))
                if process_event else bool(
                    event.payload.get("destination")
                    or event.payload.get("target")
                    or event.payload.get("device_id")
                )
            )
            for item in reversed(self._items.values()):
                if item.session_id != event.session_id or item.capture_stopped:
                    continue
                if event.event_id in item.event_ids or (
                    can_merge
                    and len(item.event_ids) < 64
                    and item.merge_key == key
                    and abs((event.occurred_at - item.occurred_at).total_seconds())
                    <= self.settings.merge_seconds
                ):
                    item.event_ids.add(event.event_id)
                    return item.evidence_id
            if len(self._items) >= self.settings.evidence_max_items:
                self.last_error = "evidence item limit reached"
                return None
            item = Evidence(
                uuid.uuid4().hex,
                event.session_id,
                event.event_id,
                event.occurred_at,
                now,
                now + timedelta(seconds=self.settings.retention_seconds),
                event.occurred_at + timedelta(seconds=self.settings.post_seconds),
                key,
                self._policy_hash,
            )
            item.event_ids.add(event.event_id)
            self._items[item.evidence_id] = item
            historical = self.ring.between(
                event.occurred_at - timedelta(
                    seconds=min(self.settings.pre_seconds, MAX_EVENT_CONTEXT_SECONDS)
                ),
                min(now, event.occurred_at),
            )
            if historical:
                for representative in reversed(
                    historical[-AUTOMATIC_FRAME_LIMITS["event_context"]:]
                ):
                    if self._append(item, representative, "event_context"):
                        self._schedule(item, representative, "event_context")
            else:
                item.reason = "no pre-event image remains in the capture buffer"
            for frame in self.ring.between(
                event.occurred_at - timedelta(seconds=self.settings.pre_seconds),
                min(now, item.capture_until),
            ):
                self._append(item, frame, "context")
            if now > item.capture_until:
                item.needs_trigger = False
                item.reason = "event arrived after its capture window; no new screenshot taken"
            self._notice(item, self._status(item))
            self._wake.set()
            return item.evidence_id

    def request(self, request: ScreenRequest) -> None:
        with self._lock:
            if request.agent_id != self.agent_id:
                return
            if request.session_id != self._session_id or request.deadline <= self._clock():
                self._respond(
                    request, ScreenStatus.UNAVAILABLE, reason="inactive or expired request"
                )
                return
            try:
                self._requests.put_nowait(request)
            except queue.Full:
                self._respond(request, ScreenStatus.UNAVAILABLE, reason="capture queue is full")
        self._wake.set()

    def _append(self, item: Evidence, frame: CapturedFrame, kind: str) -> bool:
        if any(existing.frame_id == frame.frame_id for existing, _ in item.frames):
            return True
        already_retained = any(
            f.frame_id == frame.frame_id for e in self._items.values() for f, _ in e.frames
        )
        if kind in {"event_context", "trigger", "snapshot"} and not already_retained:
            while item.frames and (
                self.evidence_bytes + len(frame.data) > self.settings.evidence_max_bytes
            ):
                candidate = next(
                    (
                        pair
                        for pair in item.frames
                        if pair[1] == "context"
                        and not any(job.frame is pair[0] for job in self._jobs.values())
                    ),
                    None,
                )
                if candidate is None:
                    break
                item.frames.remove(candidate)
                item.reason = "context shortened to reserve RAM for representative screenshot"
        if len(item.frames) >= 32 or (
            not already_retained
            and self.evidence_bytes + len(frame.data) > self.settings.evidence_max_bytes
        ):
            item.reason = "evidence RAM/frame limit reached; capture window may be incomplete"
            return False
        item.frames.append((frame, kind))
        return True

    def _status(self, item: Evidence) -> ScreenStatus:
        if item.capture_stopped:
            if any(key[0] == item.evidence_id for key in self._jobs):
                return ScreenStatus.UPLOADING
            return ScreenStatus.UPLOADED if item.stored else ScreenStatus.UNAVAILABLE
        if not item.frames and item.reason and not item.needs_trigger:
            return ScreenStatus.UNAVAILABLE
        if item.needs_trigger or self._clock() < item.capture_until:
            return ScreenStatus.CAPTURING
        if any(key[0] == item.evidence_id for key in self._jobs):
            return ScreenStatus.UPLOADING
        if item.frames and all(frame.frame_id in item.stored for frame, _ in item.frames):
            return ScreenStatus.UPLOADED
        return ScreenStatus.READY

    def _metadata(
        self, item: Evidence, frame: CapturedFrame, kind: str, request_id: str | None = None
    ) -> ScreenFrame:
        return ScreenFrame(
            protocol_version=2,
            frame_id=frame.frame_id,
            evidence_id=item.evidence_id,
            agent_id=self.agent_id,
            session_id=item.session_id,
            event_id=item.event_id,
            request_id=request_id,
            captured_at=frame.captured_at,
            event_occurred_at=item.occurred_at if item.event_id else None,
            event_received_at=item.created_at if item.event_id else None,
            available_until=item.expires_at,
            policy_hash=item.policy_hash,
            monitor_id=frame.monitor_id,
            kind=kind,
            width=frame.width,
            height=frame.height,
            size_bytes=len(frame.data),
            sha256=frame.sha256,
        )

    def _schedule(
        self, item: Evidence, frame: CapturedFrame, kind: str, request_id: str | None = None
    ) -> bool:
        key = (item.evidence_id, frame.frame_id, request_id)
        if key in self._jobs:
            return True
        if len(self._jobs) < 2048:
            self._jobs[key] = UploadJob(self._metadata(item, frame, kind, request_id), frame)
            return True
        item.reason = "screenshot upload queue is full"
        self.last_error = item.reason
        return False

    def prune(self, now: datetime | None = None) -> None:
        with self._lock:
            now = now or self._clock()
            self.ring.prune(now)
            for request_id, expiry in tuple(self._response_expiry.items()):
                if expiry <= now:
                    self._responses.pop(request_id, None)
                    del self._response_expiry[request_id]
            for evidence_id, item in tuple(self._items.items()):
                if item.expires_at <= now:
                    self._notice(item, ScreenStatus.EXPIRED)
                    del self._items[evidence_id]
                    self._notice_cache.pop(evidence_id, None)
                    for key in tuple(self._jobs):
                        if key[0] == evidence_id:
                            del self._jobs[key]

    def capture_once(self) -> None:
        """One worker tick, also exposed for deterministic integration tests."""
        with self._lock:
            session_id = self._session_id
            capture_epoch = self._capture_epoch
            self.prune()
            pending = [
                item for item in self._items.values()
                if item.session_id == session_id and not item.capture_stopped and item.needs_trigger
            ]
        if session_id is None:
            self._publish_updates()
            return
        for item in pending:
            with self._lock:
                if self._clock() > item.capture_until:
                    item.needs_trigger = False
                    item.reason = "event capture window elapsed before trigger capture"
                    self._notice(item, self._status(item))
                    continue
                if self._session_id != session_id or self._capture_epoch != capture_epoch:
                    break
            try:
                frame = self._capture(True)
                with self._lock:
                    if (
                        item.evidence_id not in self._items
                        or self._session_id != session_id
                        or self._capture_epoch != capture_epoch
                    ):
                        continue
                    item.needs_trigger = False
                    if frame.captured_at > item.capture_until:
                        item.reason = "trigger completed outside the event capture window"
                    elif self._append(item, frame, "trigger"):
                        self._schedule(item, frame, "trigger")
                    self._notice(item, self._status(item))
            except (OSError, ValueError) as exc:
                with self._lock:
                    if self._capture_epoch != capture_epoch or item.capture_stopped:
                        continue
                    item.needs_trigger = False
                    item.reason = f"trigger capture unavailable: {_format_capture_error(exc)}"
                    self._notice(item, ScreenStatus.UNAVAILABLE)
        with self._lock:
            capture_active = (
                self._session_id == session_id and self._capture_epoch == capture_epoch
            )
        if capture_active and time.monotonic() >= self._next_capture:
            self._next_capture = time.monotonic() + self.settings.interval_seconds
            try:
                frame = self._capture(False)
                with self._lock:
                    if self._session_id == session_id and self._capture_epoch == capture_epoch:
                        self.ring.add(frame)
                        for item in self._items.values():
                            if (
                                not item.capture_stopped
                                and item.session_id == session_id
                                and item.occurred_at <= frame.captured_at <= item.capture_until
                            ):
                                self._append(item, frame, "context")
                        self.last_error = None
            except (OSError, ValueError) as exc:
                self.last_error = _format_capture_error(exc)
        for _ in range(4):
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                break
            self._handle_request(request)
        self._publish_updates()

    def _publish_updates(self) -> None:
        if time.monotonic() - self._last_notice >= 5:
            self._last_notice = time.monotonic()
            with self._lock:
                for response in self._responses.values():
                    self._safe_send(response)
                for item in self._items.values():
                    self._notice(item, self._status(item))

    def _handle_request(self, request: ScreenRequest) -> None:
        with self._lock:
            if request.deadline <= self._clock() or request.session_id != self._session_id:
                self._respond(
                    request, ScreenStatus.UNAVAILABLE, reason="inactive or expired request"
                )
                return
            if request.agent_id != self.agent_id:
                return
            item = self._items.get(request.evidence_id or "")
            capture_epoch = self._capture_epoch
        if request.mode == ScreenMode.SNAPSHOT:
            try:
                frame = self._capture(True)
            except (OSError, ValueError) as exc:
                self._respond(request, ScreenStatus.UNAVAILABLE, reason=_format_capture_error(exc))
                return
            with self._lock:
                if (
                    self._session_id != request.session_id
                    or self._capture_epoch != capture_epoch
                ):
                    return
                if len(self._items) >= self.settings.evidence_max_items:
                    self._respond(request, ScreenStatus.UNAVAILABLE, reason="evidence item limit")
                    return
                now = self._clock()
                item = Evidence(
                    uuid.uuid4().hex,
                    request.session_id,
                    None,
                    now,
                    now,
                    now + timedelta(seconds=self.settings.retention_seconds),
                    now,
                    (),
                    self._policy_hash,
                    needs_trigger=False,
                )
                self._items[item.evidence_id] = item
                self._append(item, frame, "snapshot")
        with self._lock:
            if self._session_id != request.session_id or self._capture_epoch != capture_epoch:
                return
            if item is None or item.session_id != request.session_id:
                self._respond(request, ScreenStatus.EXPIRED, reason="evidence no longer in RAM")
                return
            selected: list[ScreenFrame] = []
            size = 0
            for frame, kind in sorted(item.frames, key=lambda pair: pair[0].captured_at):
                if request.start_at and frame.captured_at < request.start_at:
                    continue
                if request.end_at and frame.captured_at > request.end_at:
                    continue
                if (
                    len(selected) >= request.max_frames
                    or size + len(frame.data) > request.max_bytes
                ):
                    break
                if not self._schedule(item, frame, kind, request.request_id):
                    break
                selected.append(self._metadata(item, frame, kind, request.request_id))
                size += len(frame.data)
            self._respond(
                request,
                ScreenStatus.UPLOADING if selected else ScreenStatus.UNAVAILABLE,
                item=item,
                frames=selected,
                reason=item.reason or (None if selected else "no frames in requested window"),
            )

    def _respond(self, request, status, *, item=None, frames=None, reason=None) -> None:
        response = ScreenResponse(
            protocol_version=2,
            request_id=request.request_id,
            agent_id=self.agent_id,
            session_id=request.session_id,
            evidence_id=item.evidence_id if item else request.evidence_id,
            event_id=item.event_id if item else None,
            status=status,
            available_until=item.expires_at if item else None,
            frames=frames or [],
            reason=reason,
        )
        with self._lock:
            self._responses[response.request_id] = response
            self._response_expiry[response.request_id] = (
                item.expires_at if item else request.deadline
            )
            while len(self._responses) > 32:
                expired_id, _response = self._responses.popitem(last=False)
                self._response_expiry.pop(expired_id, None)
        self._safe_send(response)

    def _notice(self, item: Evidence, status: ScreenStatus) -> None:
        previous = self._notice_cache.get(item.evidence_id)
        now = time.monotonic()
        if previous and previous[:2] == (status, item.reason) and now - previous[2] < 30:
            return
        self._notice_cache[item.evidence_id] = (status, item.reason, now)
        self._safe_send(
            ScreenResponse(
                protocol_version=2,
                request_id=f"notice_{item.evidence_id}",
                agent_id=self.agent_id,
                session_id=item.session_id,
                evidence_id=item.evidence_id,
                event_id=item.event_id,
                status=status,
                available_until=min(self._clock(), item.expires_at)
                if item.capture_stopped else item.expires_at,
                reason=item.reason,
            )
        )

    def _safe_send(self, value: ScreenResponse) -> None:
        with suppress(OSError, queue.Full):
            self._send(value)

    def upload_once(self) -> bool:
        with self._lock:
            self.prune()
            available = [
                (key, job)
                for key, job in self._jobs.items()
                if job.next_attempt <= time.monotonic()
            ]
            if not available:
                return False
            key, job = min(available, key=lambda pair: pair[1].metadata.kind == "context")
        try:
            server_stored = self._upload(job.metadata, job.frame.data)
        except (OSError, ValueError, TimeoutError) as exc:
            server_stored = False
            self.last_error = str(exc)[:500]
            if job.attempts % 5 == 0:
                self._log(f"event=SCREEN_UPLOAD_FAILED reason={self.last_error}")
        with self._lock:
            if self._jobs.get(key) is not job:
                return True
            if server_stored:
                del self._jobs[key]
                if key[2] is not None and not any(pending[2] == key[2] for pending in self._jobs):
                    self._responses.pop(key[2], None)
                    self._response_expiry.pop(key[2], None)
                item = self._items.get(key[0])
                if item:
                    item.stored.add(job.frame.frame_id)
                    if item.capture_stopped:
                        pending_ids = {pending[1] for pending in self._jobs if pending[0] == key[0]}
                        item.frames[:] = [
                            pair for pair in item.frames if pair[0].frame_id in pending_ids
                        ]
                    self._notice(item, self._status(item))
                    if item.capture_stopped and not item.frames:
                        self._release(item.evidence_id)
            else:
                job.attempts += 1
                job.next_attempt = time.monotonic() + min(30, 2 ** min(job.attempts, 5))
        return True

    def _capture_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.capture_once()
            except Exception as exc:
                self.last_error = str(exc)[:500]
                self._log(f"event=SCREEN_CAPTURE_ERROR error_type={type(exc).__name__}")
            self._wake.wait(0.25)
            self._wake.clear()

    def _upload_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.upload_once():
                    continue
            except Exception as exc:
                self.last_error = str(exc)[:500]
                self._log(f"event=SCREEN_UPLOAD_ERROR error_type={type(exc).__name__}")
            self._stop.wait(0.25)
