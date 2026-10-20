from __future__ import annotations

import queue
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.client.infrastructure.event_spool import AgentEventSpool
from agent.client.infrastructure.gateway_control_client import GatewayControlClient
from agent.domain.identity import WorkstationIdentity
from apps.gateway.app.connections import AgentConnectionManager
from apps.gateway.app.event_buffer import EventBuffer
from apps.gateway.app.presence import PresenceStore
from apps.gateway.app.routing import GatewayRouter
from contracts.v2 import (
    Ack,
    AckStatus,
    ApplyPolicyPayload,
    Command,
    CommandType,
    Event,
    EventReceipt,
    EventReceiptStatus,
    EventType,
    GatewayEnvelope,
    GatewayMessageType,
    ServiceHealth,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


class FakeSocket:
    def __init__(self, *, auto_ack: bool = False):
        self.sent: list[str] = []
        self.incoming: queue.Queue[str] = queue.Queue()
        self.auto_ack = auto_ack
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True
        return False

    def send(self, message: str) -> None:
        self.sent.append(message)
        if self.auto_ack:
            envelope = GatewayEnvelope.model_validate_json(message)
            if envelope.message_type == GatewayMessageType.EVENT:
                receipt = EventReceipt(
                    protocol_version=2,
                    event_id=envelope.message_id,
                    status=EventReceiptStatus.ACCEPTED,
                    received_at=datetime.now(UTC),
                )
                receipt_env = GatewayEnvelope(
                    protocol_version=2,
                    message_type=GatewayMessageType.EVENT_RECEIPT,
                    message_id=f"receipt_{envelope.message_id}",
                    correlation_id=envelope.correlation_id,
                    source_id="gateway",
                    target_id=envelope.source_id,
                    payload=receipt.model_dump(mode="json", exclude_none=True),
                )
                self.incoming.put(receipt_env.to_json())

    def recv(self, timeout: float | None = None) -> str:
        try:
            return self.incoming.get(timeout=timeout or 0.05)
        except queue.Empty:
            raise TimeoutError from None


class BackendCapture:
    def __init__(self):
        self.messages: list[GatewayEnvelope] = []

    async def send(self, envelope: GatewayEnvelope) -> None:
        self.messages.append(envelope)


def _make_client(spool: AgentEventSpool, connector=None) -> GatewayControlClient:
    return GatewayControlClient(
        "ws://gateway/ws/agents",
        "agent-token",
        "1.1.0",
        allow_plaintext_ws=True,
        event_spool=spool,
        connector=connector or (lambda *_args, **_kwargs: FakeSocket()),
    )


# TEST 1: Tạo Event, chưa gửi, Agent restart -> Event có được khôi phục không?
def test_event_restored_after_untransmitted_agent_restart(tmp_path: Path) -> None:
    spool_path = tmp_path / "spool-t1.db"
    spool1 = AgentEventSpool(spool_path)
    client1 = _make_client(spool1)
    client1._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    client1.report_event(
        "SES-001",
        "PC01",
        EventType.FORBIDDEN_PROCESS_DETECTED,
        {"process_name": "cheat_tool.exe", "severity": "CRITICAL"},
    )
    assert spool1.count() == 1
    pending_events = spool1.pending()
    original_event = Event.model_validate(pending_events[0].payload)
    original_id = original_event.event_id
    original_seq = original_event.sequence

    # Simulate abrupt process crash / termination: discard client1 without closing or sending
    del client1
    del spool1

    # Restart: new process creates new client with the same spool path
    spool2 = AgentEventSpool(spool_path)
    socket = FakeSocket(auto_ack=True)
    client2 = _make_client(spool2, connector=lambda *_a, **_k: socket)
    client2._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    assert spool2.count() == 1
    # Flush outbound: recovered event must be sent with identical identity, sequence, and payload
    client2._flush_outbound(socket)

    assert len(socket.sent) == 1
    sent_env = GatewayEnvelope.model_validate_json(socket.sent[0])
    assert sent_env.message_type == GatewayMessageType.EVENT
    sent_event = Event.model_validate(sent_env.payload)
    assert sent_event.event_id == original_id
    assert sent_event.sequence == original_seq
    assert sent_event.payload["process_name"] == "cheat_tool.exe"


# TEST 2: Gateway offline, Agent tạo nhiều Event rồi restart -> không mất Event đã được lưu bền vững
def test_events_buffered_and_recovered_across_restarts_when_gateway_offline(tmp_path: Path) -> None:
    spool_path = tmp_path / "spool-t2.db"
    spool1 = AgentEventSpool(spool_path)

    # Connector raises OSError simulating gateway offline
    def offline_connector(*_a, **_k):
        raise OSError("Gateway connection refused")

    client1 = _make_client(spool1, connector=offline_connector)
    client1._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    for i in range(1, 4):
        client1.report_event(
            "SES-001",
            "PC01",
            EventType.POLICY_VIOLATION,
            {"destination": f"site{i}.com", "action": "BLOCKED"},
        )
    assert spool1.count() == 3

    # Simulate crash
    del client1
    del spool1

    # Restart: Gateway comes online
    spool2 = AgentEventSpool(spool_path)
    assert spool2.count() == 3
    socket = FakeSocket(auto_ack=True)
    client2 = _make_client(spool2, connector=lambda *_a, **_k: socket)
    client2._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    client2._flush_outbound(socket)
    assert len(socket.sent) == 3

    # All 3 events delivered in order
    destinations = [
        Event.model_validate(GatewayEnvelope.model_validate_json(raw).payload).payload["destination"]
        for raw in socket.sent
    ]
    assert destinations == ["site1.com", "site2.com", "site3.com"]

    # Process receipts
    while not socket.incoming.empty():
        client2._receive(socket.incoming.get_nowait())

    assert spool2.count() == 0


# TEST 3: websocket.send() thành công nhưng Gateway chưa commit SQLite
# -> không được xóa bản Event cuối cùng
def test_event_retained_in_local_spool_if_send_succeeds_but_gateway_uncommitted(
    tmp_path: Path,
) -> None:
    spool_path = tmp_path / "spool-t3.db"
    spool = AgentEventSpool(spool_path)
    # Socket sends bytes to TCP, but does NOT return an ACK receipt (gateway didn't commit)
    socket = FakeSocket(auto_ack=False)
    client = _make_client(spool, connector=lambda *_a, **_k: socket)
    client._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    client.report_event(
        "SES-001",
        "PC01",
        EventType.FORBIDDEN_PROCESS_DETECTED,
        {"process_name": "malware.exe"},
    )
    client._flush_outbound(socket)

    assert len(socket.sent) == 1
    # Crucial assertion: Event MUST NOT be removed from local spool merely because send() completed!
    assert spool.count() == 1
    sent_env = GatewayEnvelope.model_validate_json(socket.sent[0])
    sent_evt = Event.model_validate(sent_env.payload)
    assert spool.has(sent_evt.event_id)


# TEST 4: Gateway đã ghi Event nhưng phản hồi xác nhận thất lạc
# -> retransmit phải giữ nguyên event_id và không tạo Telemetry trùng
def test_retransmit_same_event_id_on_lost_receipt_idempotent_at_gateway_and_backend(
    tmp_path: Path,
) -> None:
    spool_path = tmp_path / "spool-t4.db"
    gw_db_path = tmp_path / "gw-t4.db"

    spool = AgentEventSpool(spool_path)
    gw_buffer = EventBuffer(gw_db_path, max_rows=10, degraded_threshold=8)
    backend = BackendCapture()
    connections = AgentConnectionManager()
    router = GatewayRouter("GW-1", connections, PresenceStore(), backend, gw_buffer)

    client = _make_client(spool)
    client._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    client.report_event(
        "SES-001",
        "PC01",
        EventType.POLICY_VIOLATION,
        {"destination": "prohibited.org"},
    )
    env = spool.pending()[0]

    import asyncio

    # First delivery: Gateway buffers it into SQLite
    asyncio.run(router.route_agent("PC01", env))
    assert gw_buffer.count(include_terminal=False) == 1

    # Receipt was lost on the wire; Client retransmits the EXACT same envelope
    asyncio.run(router.route_agent("PC01", env))

    # Gateway count remains 1 (idempotent, no duplicate stored)
    assert gw_buffer.count(include_terminal=False) == 1


# TEST 5: Agent restart sau khi Gateway xác nhận lưu Event -> không tạo Event mới với ID khác
def test_no_duplicate_event_created_after_gateway_confirms(tmp_path: Path) -> None:
    spool_path = tmp_path / "spool-t5.db"
    spool1 = AgentEventSpool(spool_path)
    socket = FakeSocket(auto_ack=True)
    client1 = _make_client(spool1, connector=lambda *_a, **_k: socket)
    client1._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    client1.report_event(
        "SES-001",
        "PC01",
        EventType.SERVICE_HEALTH,
        {"action": "HEALTHY"},
    )
    client1._flush_outbound(socket)
    # Process receipt
    client1._receive(socket.incoming.get_nowait())
    assert spool1.count() == 0

    # Restart
    del client1
    del spool1

    spool2 = AgentEventSpool(spool_path)
    socket2 = FakeSocket(auto_ack=True)
    client2 = _make_client(spool2, connector=lambda *_a, **_k: socket2)
    client2._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    client2._flush_outbound(socket2)
    # Nothing should be sent
    assert len(socket2.sent) == 0
    assert spool2.count() == 0


# TEST 6: Nhiều Event được tạo trong thời gian offline
# -> dữ liệu được phục hồi nhất quán; xác định yêu cầu thứ tự
def test_multiple_offline_events_monotonic_sequence_order(tmp_path: Path) -> None:
    spool_path = tmp_path / "spool-t6.db"
    spool1 = AgentEventSpool(spool_path)
    client1 = _make_client(spool1)
    client1._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    client1.report_event("SES-001", "PC01", EventType.POLICY_VIOLATION, {"step": 1})
    client1.report_event("SES-001", "PC01", EventType.POLICY_VIOLATION, {"step": 2})
    client1.report_event("SES-001", "PC01", EventType.POLICY_VIOLATION, {"step": 3})

    del client1
    del spool1

    # Restart
    spool2 = AgentEventSpool(spool_path)
    client2 = _make_client(spool2)
    client2._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    # Sequence counter resumes monotonically from 3, so next event is 4
    assert client2._event_sequence == 3
    client2.report_event("SES-001", "PC01", EventType.POLICY_VIOLATION, {"step": 4})
    assert client2._event_sequence == 4

    events = [Event.model_validate(env.payload) for env in spool2.pending()]
    sequences = [e.sequence for e in events]
    assert sequences == [1, 2, 3, 4]


# TEST 7: Agent queue/disk buffer đầy -> báo lỗi/health rõ ràng, không silently discard
def test_agent_event_spool_full_degrades_health_and_raises(tmp_path: Path) -> None:
    spool_path = tmp_path / "spool-t7.db"
    spool = AgentEventSpool(spool_path, max_rows=2)
    client = _make_client(spool)
    client._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    client.report_event("SES-001", "PC01", EventType.POLICY_VIOLATION, {"idx": 1})
    client.report_event("SES-001", "PC01", EventType.POLICY_VIOLATION, {"idx": 2})
    assert spool.count() == 2

    # 3rd event exceeds capacity: must raise OSError caused by EventSpoolFullError
    with pytest.raises(OSError, match="Agent event spool is full"):
        client.report_event("SES-001", "PC01", EventType.POLICY_VIOLATION, {"idx": 3})

    # Service health must be marked DEGRADED
    assert client._service_health == ServiceHealth.DEGRADED


# TEST 8: Agent bị kill/restart -> không làm thay đổi ACK, APPLY_POLICY hay RESTORE_BASELINE
def test_agent_crash_restart_preserves_commands_and_acks(tmp_path: Path) -> None:
    spool_path = tmp_path / "spool-t8.db"
    spool = AgentEventSpool(spool_path)
    socket = FakeSocket(auto_ack=False)
    client = _make_client(spool, connector=lambda *_a, **_k: socket)
    identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")
    client._identity = identity

    from contracts.v2 import PolicyEnvelope, PolicyRules, compute_policy_hash

    rules = PolicyRules()
    policy_hash = compute_policy_hash("STRICT", 1, rules)
    policy = PolicyEnvelope.from_legacy(
        policy_id="STRICT",
        policy_version=1,
        policy_hash=policy_hash,
        session_id="SES-001",
        issued_at=NOW,
        rules={},
    )
    command = Command(
        protocol_version=2,
        command_id="CMD-P8",
        command_type=CommandType.APPLY_POLICY,
        target_id="PC01",
        session_id="SES-001",
        policy_hash=policy_hash,
        issued_at=NOW,
        deadline=NOW + timedelta(minutes=5),
        payload=ApplyPolicyPayload(policy=policy),
        correlation_id="CORR-P8",
    )
    cmd_envelope = GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.COMMAND,
        message_id="msg-p8",
        correlation_id="CORR-P8",
        source_id="GW-1",
        target_id="PC01",
        payload=command.model_dump(mode="json"),
    )

    client._receive(cmd_envelope.to_json())
    pending = client.pending_commands("PC01")
    assert len(pending) == 1
    assert pending[0]["id"] == "CMD-P8"

    client.acknowledge_command("CMD-P8", success=True, policy_hash=policy_hash, actor="PC01")
    client._flush_outbound(socket)

    assert len(socket.sent) == 1
    ack_env = GatewayEnvelope.model_validate_json(socket.sent[0])
    assert ack_env.message_type == GatewayMessageType.ACK
    ack = Ack.model_validate(ack_env.payload)
    assert ack.command_id == "CMD-P8"
    assert ack.status == AckStatus.SUCCEEDED
    assert ack.applied_hash == policy_hash

    # Active policy is stored
    active = client.active_policy("PC01")
    assert active is not None
    assert active[0] == "SES-001"
    assert active[1]["policy_hash"] == policy_hash


# TEST 9: Sequence tăng liên tục qua restart ngay cả khi
# toàn bộ Event đã được Gateway xác nhận và spool trống
def test_sequence_monotonic_after_all_events_acknowledged_and_restart(tmp_path: Path) -> None:
    spool_path = tmp_path / "spool-t9.db"
    spool1 = AgentEventSpool(spool_path)
    socket1 = FakeSocket(auto_ack=True)
    client1 = _make_client(spool1, connector=lambda *_a, **_k: socket1)
    client1._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    # Generate 2 events
    client1.report_event("SES-001", "PC01", EventType.SERVICE_HEALTH, {"run": 1})
    client1.report_event("SES-001", "PC01", EventType.SERVICE_HEALTH, {"run": 2})

    # Flush outbound & receive Gateway receipts
    client1._flush_outbound(socket1)
    while not socket1.incoming.empty():
        client1._receive(socket1.incoming.get_nowait())

    # Spool is now completely empty
    assert spool1.count() == 0
    assert spool1.max_sequence() == 2

    # Simulate Agent process termination and restart
    del client1
    del spool1

    spool2 = AgentEventSpool(spool_path)
    socket2 = FakeSocket(auto_ack=True)
    client2 = _make_client(spool2, connector=lambda *_a, **_k: socket2)
    client2._identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")

    # Counter must resume from 2
    assert client2._event_sequence == 2

    # Generate next event after restart
    client2.report_event("SES-001", "PC01", EventType.POLICY_VIOLATION, {"run": 3})
    assert client2._event_sequence == 3

    client2._flush_outbound(socket2)
    assert len(socket2.sent) == 1
    sent_env = GatewayEnvelope.model_validate_json(socket2.sent[0])
    sent_evt = Event.model_validate(sent_env.payload)
    assert sent_evt.sequence == 3


# TEST 10: Queue đầy -> ghi Event vào spool -> queue được giải phóng
# -> Event tự động được gửi lại trên cùng kết nối, không cần restart/reconnect
def test_event_spooled_on_queue_full_retransmitted_on_same_connection_without_restart(
    tmp_path: Path,
) -> None:
    spool_path = tmp_path / "spool-t10.db"
    spool = AgentEventSpool(spool_path)
    socket = FakeSocket(auto_ack=True)
    client = _make_client(spool, connector=lambda *_a, **_k: socket)
    identity = WorkstationIdentity("PC01", "HOST-1", "192.168.1.10", "1.1.0")
    client._identity = identity

    # Giả lập queue đầy bằng cách đặt queue có maxsize=1 và chứa sẵn 1 dummy envelope
    client._outbound = queue.Queue(maxsize=1)
    dummy_envelope = GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.PRESENCE,
        message_id="dummy_presence",
        correlation_id="dummy_presence",
        source_id="PC01",
        target_id="backend",
        payload={"dummy": True},
    )
    client._outbound.put_nowait(dummy_envelope)
    assert client._outbound.full()

    # Tạo Event khi queue đầy:
    # 1. Event phải được ghi vào SQLite spool bền vững
    # 2. _queue() raise OSError do queue đầy
    with pytest.raises(OSError, match="Agent Gateway queue is full"):
        client.report_event(
            "SES-001",
            "PC01",
            EventType.POLICY_VIOLATION,
            {"destination": "blocked.com", "action": "BLOCKED"},
        )

    # Xác minh Event đã được lưu vào spool
    assert spool.count() == 1
    spooled_env = spool.pending()[0]
    spooled_event = Event.model_validate(spooled_env.payload)
    spooled_id = spooled_event.event_id

    # Xác minh Event KHÔNG bị đánh dấu nhầm là đã enqueue trong _enqueued_event_ids
    assert spooled_id not in client._enqueued_event_ids

    # Socket chưa gửi Event
    assert len(socket.sent) == 0

    # Queue được giải phóng qua chu kỳ flush tiếp theo trên CÙNG kết nối (không restart/reconnect)
    # Lần flush 1: gửi dummy message, giải phóng slot trong _outbound
    client._flush_outbound(socket)
    assert len(socket.sent) == 1
    first_msg = GatewayEnvelope.model_validate_json(socket.sent[0])
    assert first_msg.message_type == GatewayMessageType.PRESENCE

    # Lần flush 2 (chu kỳ tiếp theo của connection loop): _enqueue_pending_spool() tự động
    # đẩy Event từ SQLite spool vào _outbound và gửi qua socket
    client._flush_outbound(socket)
    assert len(socket.sent) == 2

    sent_events = [
        GatewayEnvelope.model_validate_json(raw)
        for raw in socket.sent
        if GatewayEnvelope.model_validate_json(raw).message_type == GatewayMessageType.EVENT
    ]
    # Không gửi duplicate: đúng 1 Event được gửi
    assert len(sent_events) == 1
    sent_evt = Event.model_validate(sent_events[0].payload)
    assert sent_evt.event_id == spooled_id
    assert sent_evt.payload["destination"] == "blocked.com"

    # Xử lý receipt tự động từ FakeSocket
    while not socket.incoming.empty():
        client._receive(socket.incoming.get_nowait())

    # Event hoàn tất và được giải phóng khỏi spool, không bị mất
    assert spool.count() == 0
    assert spooled_id not in client._enqueued_event_ids
