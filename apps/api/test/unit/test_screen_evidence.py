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


def event(index=0, **payload):
    return Event(
        protocol_version=2,
        event_id=f"event_{index}",
        session_id="ses1",
        agent_id="PC01",
        occurred_at=NOW,
        event_type=EventType.POLICY_VIOLATION,
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


def test_pin_survives_ring_rotation_and_only_trigger_uploads_automatically():
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
    assert [value.kind for value in sent] == ["trigger"]
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


def test_session_change_clears_old_session_images_and_jobs():
    manager, _, _, _ = make_manager()
    manager.on_event(event())
    manager.capture_once()
    manager.activate("ses2", {})
    assert not manager._items and not manager._jobs and manager.ring.latest() is None


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


def test_capture_requires_running_session_authority_and_stop_clears_ram():
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
    manager.activate("ses1", {})  # An old policy ACK cannot restart capture after FINISHED.
    manager.capture_once()
    assert not manager._items and not manager._jobs and manager.ring.latest() is None


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
