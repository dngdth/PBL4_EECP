from datetime import UTC, datetime, timedelta

import pytest

from agent.client.infrastructure.gateway_control_client import GatewayControlClient
from agent.client.screen.buffer import RingBuffer
from agent.client.screen.capture import CapturedFrame, ScreenSettings
from agent.client.screen.manager import EvidenceManager
from agent.domain.identity import WorkstationIdentity
from contracts.v2 import Event, EventType, GatewayEnvelope
from contracts.v2.screen import ScreenRequest, ScreenResponse

NOW = datetime(2026, 11, 6, 9, tzinfo=UTC)


def frame(index=0, size=100):
    return CapturedFrame(f"frame_{index}", NOW + timedelta(seconds=index), b"x" * size, 100, 50)


def event(index=0, *, occurred_at=NOW, event_type=EventType.POLICY_VIOLATION, **payload):
    return Event(
        protocol_version=2,
        event_id=f"event_{index}",
        session_id="ses1",
        agent_id="PC01",
        occurred_at=occurred_at,
        event_type=event_type,
        payload={"severity": "WARNING", "destination": "chatgpt.com", **payload},
    )


def make_manager(**kwargs):
    now = [NOW]
    captures = []
    notices = []

    def capture(high):
        captures.append(high)
        return CapturedFrame(f"frame_{len(captures)}", now[0], b"x" * 100, 100, 50)

    manager = EvidenceManager(
        "PC01",
        notices.append,
        kwargs.pop("upload", lambda *_: True),
        capture=capture,
        clock=lambda: now[0],
        **kwargs,
    )
    manager.activate("ses1", {})
    return manager, now, captures, notices


def request(now=NOW, **updates):
    return ScreenRequest.model_validate(
        {
            "protocol_version": 2,
            "request_id": "req1",
            "agent_id": "PC01",
            "session_id": "ses1",
            "mode": "SNAPSHOT",
            "issued_at": now,
            "deadline": now + timedelta(seconds=60),
            **updates,
        }
    )


def test_ring_enforces_time_count_and_bytes_and_rejects_oversized_frame():
    ring = RingBuffer(30, 2, 250)
    for index in range(3):
        ring.add(frame(index))
    assert [value.frame_id for value in ring.between(NOW, NOW + timedelta(seconds=9))] == [
        "frame_1",
        "frame_2",
    ]
    assert ring.size_bytes == 200
    ring.add(frame(3, 1000))
    assert ring.size_bytes == 200
    ring.prune(NOW + timedelta(seconds=33))
    assert ring.latest() is None
    assert ring.size_bytes == 0


def test_pin_survives_ring_rotation_and_only_representatives_upload_automatically():
    sent = []
    manager, now, captures, _ = make_manager(upload=lambda meta, data: sent.append(meta) or True)
    manager.capture_once()
    pinned = manager.ring.latest()
    evidence_id = manager.on_event(event())
    manager.capture_once()
    assert captures == [False, True]
    now[0] += timedelta(seconds=40)
    manager._next_capture = 0
    manager.capture_once()
    assert pinned is not manager.ring.latest()
    assert manager._items[evidence_id].frames[0][0] is pinned
    assert manager.upload_once()
    assert manager.upload_once()
    assert [value.kind for value in sent] == ["event_context", "trigger"]
    assert not manager.upload_once()


def test_default_capture_interval_is_three_seconds_and_env_can_override(monkeypatch):
    monkeypatch.delenv("EECP_SCREEN_INTERVAL_SECONDS", raising=False)
    assert ScreenSettings().interval_seconds == 3
    assert ScreenSettings.from_env().interval_seconds == 3
    monkeypatch.setenv("EECP_SCREEN_INTERVAL_SECONDS", "4")
    assert ScreenSettings.from_env().interval_seconds == 4


def test_periodic_capture_waits_three_seconds(monkeypatch):
    monotonic = [100.0]
    monkeypatch.setattr("agent.client.screen.manager.time.monotonic", lambda: monotonic[0])
    manager, now, captures, _ = make_manager()
    manager.capture_once()
    monotonic[0] += 2.9
    now[0] += timedelta(seconds=2.9)
    manager.capture_once()
    assert captures == [False]
    monotonic[0] += 0.1
    now[0] += timedelta(seconds=0.1)
    manager.capture_once()
    assert captures == [False, False]


def test_event_automatically_uploads_four_closest_history_frames_and_one_trigger():
    sent = []
    manager, now, _, _ = make_manager(upload=lambda meta, _: sent.append(meta) or True)
    for offset in (-15, -12, -9, -6, -3):
        manager.ring.add(frame(offset))
    evidence_id = manager.on_event(event())
    manager.capture_once()
    while manager.upload_once():
        pass
    assert len(sent) == 5
    assert {value.frame_id for value in sent if value.kind == "event_context"} == {
        "frame_-12", "frame_-9", "frame_-6", "frame_-3"
    }
    assert sum(value.kind == "trigger" for value in sent) == 1
    assert all(value.evidence_id == evidence_id for value in sent)
    # Uploading or capturing later must not send the rest of the ring automatically.
    now[0] += timedelta(seconds=3)
    manager._next_capture = 0
    manager.capture_once()
    assert not manager.upload_once()


def test_post_window_and_replay_respect_frame_and_byte_limits():
    manager, now, _, notices = make_manager()
    manager.capture_once()
    evidence_id = manager.on_event(event())
    manager.capture_once()
    now[0] += timedelta(seconds=2)
    manager._next_capture = 0
    manager.capture_once()
    manager.request(
        request(now[0], mode="REPLAY", evidence_id=evidence_id, max_frames=2, max_bytes=200)
    )
    manager.capture_once()
    response = next(value for value in notices if value.request_id == "req1")
    assert response.status == "uploading"
    assert len(response.frames) == 2
    assert sum(value.size_bytes for value in response.frames) <= 200
    assert all(value.evidence_id == evidence_id for value in response.frames)


def test_events_merge_and_have_stable_identity_but_distinct_targets_do_not():
    manager, _, _, _ = make_manager()
    first = manager.on_event(event())
    assert manager.on_event(event(1)) == first
    assert manager.on_event(event(2, destination="claude.ai")) != first
    assert len(manager._items) == 2


def test_client_retains_upload_until_server_ack_not_only_gateway_receipt():
    acknowledgements = [False, True]
    manager, _, _, _ = make_manager(upload=lambda *_: acknowledgements.pop(0))
    manager.on_event(event())
    manager.capture_once()
    assert manager.upload_once()
    assert len(manager._jobs) == 1
    next(iter(manager._jobs.values())).next_attempt = 0
    assert manager.upload_once()
    assert len(manager._jobs) == 0


def test_expiry_releases_evidence_and_pending_upload_memory():
    manager, now, _, notices = make_manager(upload=lambda *_: False)
    manager.on_event(event())
    manager.capture_once()
    now[0] += timedelta(seconds=901)
    manager.prune()
    assert not manager._items
    assert not manager._jobs
    assert manager.evidence_bytes == 0
    assert notices[-1].status == "expired"


def test_global_ram_cap_and_item_cap_are_enforced():
    manager, _, _, _ = make_manager(
        settings=ScreenSettings(evidence_max_bytes=150, evidence_max_items=1)
    )
    manager.capture_once()
    evidence_id = manager.on_event(event())
    manager.capture_once()
    assert manager.evidence_bytes <= 150
    assert manager._items[evidence_id].reason is not None
    assert manager.on_event(event(1, destination="other.example")) is None


def test_wrong_session_and_expired_requests_never_capture():
    manager, _, captures, notices = make_manager()
    manager.request(request(session_id="other"))
    manager.capture_once()
    assert captures == [False]
    assert next(value for value in notices if value.request_id == "req1").status == "unavailable"


def test_capture_failure_does_not_stop_context_worker_or_create_fake_image():
    manager, _, _, notices = make_manager()
    manager._capture = lambda _: (_ for _ in ()).throw(OSError("desktop locked"))
    evidence_id = manager.on_event(event())
    manager.capture_once()
    assert not manager._items[evidence_id].frames
    assert not manager._jobs
    assert "desktop locked" in manager.last_error
    assert any(value.status == "unavailable" for value in notices)


def test_session_change_preserves_queued_images_without_capturing_old_session():
    sent = []
    manager, _, _, _ = make_manager(upload=lambda meta, _: sent.append(meta) or True)
    manager.on_event(event())
    manager.capture_once()
    manager.activate("ses2", {})
    assert manager.ring.latest() is None
    assert all(item.capture_stopped for item in manager._items.values())
    assert manager.upload_once()
    assert sent[0].session_id == "ses1"
    assert not manager._items and not manager._jobs


def test_contract_rejects_unbounded_and_mismatched_requests():
    with pytest.raises(ValueError):
        request(mode="REPLAY")
    with pytest.raises(ValueError):
        request(max_frames=100000)
    with pytest.raises(ValueError):
        request(deadline=NOW + timedelta(hours=1))
    with pytest.raises(ValueError):
        ScreenResponse(
            protocol_version=2,
            request_id="../escape",
            agent_id="PC01",
            session_id="ses1",
            status="ready",
        )


def test_control_event_hook_links_evidence_without_placing_images_on_control_queue():
    client = GatewayControlClient("ws://gateway/ws/agents", "token", "1", allow_plaintext_ws=True)
    client._identity = WorkstationIdentity("PC01", "HOST", "192.0.2.1", "1")
    client.screen_event_handler = lambda event: "evidence1"
    client.report_event(
        "ses1",
        "PC01",
        EventType.POLICY_VIOLATION,
        {"destination": "chatgpt.com"},
        event_id="event1",
    )
    envelope = client._outbound.get_nowait()
    assert envelope.payload["payload"]["evidence_id"] == "evidence1"
    assert envelope.message_type == "EVENT"
    assert len(envelope.to_json()) < 2048


def test_screen_request_reaches_capture_worker_and_rejects_forged_source():
    client = GatewayControlClient("ws://gateway/ws/agents", "token", "1", allow_plaintext_ws=True)
    client._identity = WorkstationIdentity("PC01", "HOST", "192.0.2.1", "1")
    received = []
    client.screen_request_handler = received.append
    control = request()
    envelope = GatewayEnvelope(
        protocol_version=2,
        message_type="SCREEN_REQUEST",
        message_id="req1",
        correlation_id="req1",
        source_id="backend",
        target_id="PC01",
        payload=control.model_dump(mode="json"),
    )
    client._receive(envelope.to_json())
    assert received == [control]
    with pytest.raises(ValueError, match="identity"):
        client._receive(envelope.model_copy(update={"source_id": "other"}).to_json())


def test_capture_authority_stops_capture_but_drains_already_queued_images():
    manager, _, captures, _ = make_manager(require_session_authorization=True)
    manager.capture_once()
    assert not captures
    manager.authorize_session("ses1")
    manager.activate("ses1", {})
    manager.capture_once()
    assert captures == [False]
    manager.on_event(event())
    manager.capture_once()
    manager.authorize_session(None)
    capture_count = len(captures)
    manager.activate("ses1", {})  # An old policy ACK cannot restart capture after FINISHED.
    manager.capture_once()
    assert len(captures) == capture_count
    assert manager.ring.latest() is None
    assert manager._jobs
    while manager.upload_once():
        pass
    assert not manager._items and not manager._jobs


def test_closest_pre_event_image_is_queued_before_trigger_worker_runs():
    sent = []
    manager, now, _, _ = make_manager(upload=lambda meta, _: sent.append(meta) or True)
    manager.ring.add(frame(-5))
    manager.ring.add(frame(-1))
    manager.ring.add(frame(1))  # A later image must not be labelled pre-event.
    now[0] += timedelta(seconds=3)
    evidence_id = manager.on_event(event())
    assert manager.upload_once()
    assert sent[0].kind == "event_context"
    assert sent[0].frame_id == "frame_-1"
    assert sent[0].captured_at == NOW - timedelta(seconds=1)
    assert sent[0].event_occurred_at == NOW
    assert sent[0].event_received_at == now[0]
    assert sent[0].evidence_id == evidence_id


def test_delayed_event_uses_history_without_taking_a_current_trigger():
    sent = []
    manager, now, captures, _ = make_manager(upload=lambda meta, _: sent.append(meta) or True)
    manager.ring.add(frame(-2))
    now[0] += timedelta(seconds=20)
    evidence_id = manager.on_event(event())
    manager.capture_once()
    assert True not in captures
    assert not manager._items[evidence_id].needs_trigger
    assert manager.upload_once()
    assert sent[0].kind == "event_context"
    assert sent[0].captured_at < sent[0].event_occurred_at


def test_event_outside_history_reports_unavailable_instead_of_current_image():
    manager, now, captures, notices = make_manager()
    now[0] += timedelta(seconds=120)
    manager.on_event(event())
    manager.capture_once()
    assert True not in captures
    assert not manager._jobs
    assert any(value.status == "unavailable" for value in notices)


def test_late_event_batch_does_not_merge_distant_occurrence_times():
    manager, now, _, _ = make_manager()
    now[0] += timedelta(minutes=3)
    first = manager.on_event(event())
    second = manager.on_event(event(1, occurred_at=NOW + timedelta(minutes=2)))
    assert first != second


def test_process_merge_requires_matching_pid_and_creation_time():
    manager, _, _, _ = make_manager()
    base = dict(
        event_type=EventType.FORBIDDEN_PROCESS_DETECTED,
        process_name="denied.exe", pid=12, process_creation_time="created-1",
    )
    first = manager.on_event(event(0, **base))
    assert manager.on_event(event(1, **base)) == first
    assert manager._items[first].event_ids == {"event_0", "event_1"}
    assert manager.on_event(event(2, **{**base, "pid": 13})) != first
    assert manager.on_event(event(3, **{**base, "process_creation_time": "created-2"})) != first
    missing = {**base, "pid": None, "process_creation_time": None}
    assert manager.on_event(event(4, **missing)) != manager.on_event(event(5, **missing))


def test_same_event_replay_is_idempotent_after_merge():
    manager, _, _, _ = make_manager()
    first = manager.on_event(event())
    assert manager.on_event(event(1)) == first
    assert manager.on_event(event(1)) == first
    assert manager._items[first].event_ids == {"event_0", "event_1"}


def test_stop_releases_unrequested_context_but_retries_queued_images():
    calls = []
    manager, now, captures, _ = make_manager(upload=lambda meta, _: calls.append(meta) or False)
    manager.capture_once()
    manager.on_event(event())
    manager.capture_once()
    now[0] += timedelta(seconds=2)
    manager._next_capture = 0
    manager.capture_once()
    manager.deactivate()
    count = len(captures)
    assert all(kind != "context" for item in manager._items.values() for _, kind in item.frames)
    assert manager.upload_once()
    manager.capture_once()
    assert len(captures) == count
    assert manager._jobs
    now[0] += timedelta(seconds=901)
    manager.capture_once()
    assert not manager._jobs and manager.evidence_bytes == 0


def test_stop_during_capture_discards_in_flight_image_and_stops_further_grabs():
    manager, now, captures, _ = make_manager()

    def finish_during_grab(high):
        captures.append(high)
        manager.deactivate()
        return CapturedFrame("late", now[0], b"x", 1, 1)

    manager._capture = finish_during_grab
    manager.on_event(event())
    manager.capture_once()
    assert captures == [True]
    assert not manager._jobs and not manager._items and manager.ring.latest() is None


def test_stopped_snapshot_manifest_is_retried_without_new_capture():
    manager, _, captures, notices = make_manager(upload=lambda *_: False)
    manager.request(request())
    manager.capture_once()
    manager.deactivate()
    notices.clear()
    count = len(captures)
    manager._last_notice = 0
    manager.capture_once()
    assert len(captures) == count
    assert any(value.request_id == "req1" and value.frames for value in notices)
    assert manager._jobs


def test_new_session_does_not_add_frames_to_draining_old_evidence():
    manager, now, _, _ = make_manager(upload=lambda *_: False)
    first = manager.on_event(event())
    manager.capture_once()
    manager.activate("ses2", {})
    old_ids = [value.frame_id for value, _ in manager._items[first].frames]
    now[0] += timedelta(seconds=1)
    manager.capture_once()
    assert [value.frame_id for value, _ in manager._items[first].frames] == old_ids
    assert all(job.metadata.session_id == "ses1" for job in manager._jobs.values())


def test_inactive_requests_are_rejected_immediately_without_restarting_capture():
    manager, _, captures, notices = make_manager()
    manager.deactivate()
    manager.request(request())
    assert notices[-1].request_id == "req1"
    assert notices[-1].status == "unavailable"
    assert manager._requests.empty()
    manager.capture_once()
    assert not captures


def test_worker_delay_past_event_window_does_not_take_a_trigger():
    manager, now, captures, notices = make_manager()
    manager.on_event(event())
    now[0] += timedelta(seconds=20)
    manager.capture_once()
    assert True not in captures
    assert not manager._jobs
    assert any(value.status == "unavailable" for value in notices)


def test_full_upload_queue_does_not_advertise_unscheduled_replay_frames(monkeypatch):
    manager, _, _, notices = make_manager()
    manager.capture_once()
    evidence_id = manager.on_event(event())
    manager.capture_once()
    monkeypatch.setattr(manager, "_schedule", lambda *_: False)
    manager.request(request(mode="REPLAY", evidence_id=evidence_id))
    manager.capture_once()
    response = next(value for value in notices if value.request_id == "req1")
    assert response.status == "unavailable" and response.frames == []


def test_screen_session_control_ignores_stale_enable_after_stop():
    from contracts.v2.screen import ScreenSessionState

    client = GatewayControlClient("ws://gateway/ws/agents", "token", "1", allow_plaintext_ws=True)
    client._identity = WorkstationIdentity("PC01", "HOST", "192.0.2.1", "1")
    states = []
    client.screen_session_handler = lambda state: states.append(state.session_id)

    def message(at, session_id):
        state = ScreenSessionState(
            protocol_version=2, agent_id="PC01", session_id=session_id, observed_at=at
        )
        return GatewayEnvelope(
            protocol_version=2,
            message_type="SCREEN_SESSION_STATE",
            message_id="state1",
            correlation_id="state1",
            source_id="backend",
            target_id="PC01",
            payload=state.model_dump(mode="json"),
        ).to_json()

    client._receive(message(NOW, "ses1"))
    client._receive(message(NOW + timedelta(seconds=1), None))
    client._receive(message(NOW, "ses1"))
    assert states == ["ses1", None]


def test_screen_uploader_validates_ca_only_for_wss_and_surfaces_tls_errors(monkeypatch):
    import hashlib
    import ssl
    import urllib.error
    import urllib.request

    from agent.client.screen.transport import ScreenUploader
    from contracts.v2.screen import ScreenFrame

    plaintext = ScreenUploader("ws://gateway/ws/agents", "tok", ca_file="/nonexistent/ca.crt")
    assert plaintext.context is None

    with pytest.raises(FileNotFoundError, match="Gateway CA certificate not found"):
        ScreenUploader("wss://gateway:8443/ws/agents", "tok", ca_file="/nonexistent/ca.crt")

    data = b"webp"
    metadata = ScreenFrame(
        protocol_version=2,
        frame_id="frame_1",
        evidence_id="ev1",
        event_id="event_1",
        agent_id="PC01",
        session_id="ses1",
        kind="trigger",
        captured_at=NOW,
        available_until=NOW + timedelta(seconds=60),
        content_type="image/webp",
        width=100,
        height=50,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    uploader = ScreenUploader("ws://gateway/ws/agents", "tok")

    def raise_tls(*_args, **_kwargs):
        raise urllib.error.URLError(ssl.SSLError("CERTIFICATE_VERIFY_FAILED"))

    monkeypatch.setattr(urllib.request, "urlopen", raise_tls)
    with pytest.raises(OSError, match="TLS verification failed"):
        uploader(metadata, data)

    def raise_http(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            uploader.url, 409, "NTP time sync mismatch", hdrs=None, fp=None
        )

    monkeypatch.setattr(urllib.request, "urlopen", raise_http)
    with pytest.raises(OSError, match=r"rejected \(409\): NTP time sync mismatch"):
        uploader(metadata, data)


def test_evidence_manager_logs_tls_failure_reason():
    logs = []

    def fail_upload(*_args):
        raise OSError("screenshot upload TLS verification failed: CERTIFICATE_VERIFY_FAILED")

    manager, _, _, _ = make_manager(upload=fail_upload, log=logs.append)
    manager.capture_once()
    manager.on_event(event(1))

    assert manager.upload_once() is True
    assert manager.last_error == (
        "screenshot upload TLS verification failed: CERTIFICATE_VERIFY_FAILED"
    )
    assert any(
        "event=SCREEN_UPLOAD_FAILED" in line and "TLS verification failed" in line
        for line in logs
    )


def test_ensure_process_dpi_aware_safe_execution(monkeypatch):
    import sys

    from agent.client.screen import capture

    monkeypatch.setattr(capture, "_DPI_AWARE_INITIALIZED", False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert capture.ensure_process_dpi_aware() is False

    monkeypatch.setattr(capture, "_DPI_AWARE_INITIALIZED", True)
    assert capture.ensure_process_dpi_aware() is True


def test_evidence_manager_identifies_locked_desktop_reason():
    from contracts.v2.screen import ScreenMode, ScreenStatus

    responses = []

    def fail_capture(_high):
        raise OSError("secure/locked desktop cannot be captured")

    manager = EvidenceManager(
        "PC01",
        responses.append,
        lambda *_: True,
        capture=fail_capture,
        clock=lambda: NOW,
    )
    manager.activate("ses1", {})

    # 1. Periodic capture
    manager.capture_once()
    assert "desktop locked or secure" in manager.last_error

    # 2. Trigger capture
    ev_id = manager.on_event(event(1))
    manager.capture_once()
    assert (
        "desktop locked or secure" in manager._items[ev_id].reason
    )

    # 3. Snapshot request
    snap_req = request(mode=ScreenMode.SNAPSHOT)
    manager.request(snap_req)
    manager.capture_once()
    snap_resp = next(r for r in responses if r.request_id == snap_req.request_id)
    assert snap_resp.status == ScreenStatus.UNAVAILABLE
    assert "desktop locked or secure" in snap_resp.reason



