from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.gateways import BindAgentToGatewayInput, RegisterGatewayInput
from app.config import Settings
from app.domain.entities.exam_session import ExamSession
from app.domain.value_objects.enums import SessionState
from app.infrastructure.di.container import build_container
from app.presentation.api.routers.gateways import _handle_gateway_message

from apps.gateway.app.application.event_flusher import EventFlusher
from apps.gateway.app.application.routing import GatewayRouter
from apps.gateway.app.connections import AgentConnectionManager
from apps.gateway.app.event_buffer import EventBuffer, EventBufferFullError
from apps.gateway.app.presence import PresenceStore
from contracts.v2 import (
    AgentHello,
    ErrorCode,
    Event,
    EventReceipt,
    EventReceiptStatus,
    EventType,
    GatewayEnvelope,
    GatewayMessageType,
    PresenceHealth,
)

NOW = datetime.now(UTC)


def _make_event(
    event_id: str,
    agent_id: str = "PC01",
    session_id: str = "SES-001",
    *,
    occurred_at: datetime = NOW,
    category: str = "PROHIBITED_WEBSITE",
) -> GatewayEnvelope:
    event = Event(
        protocol_version=2,
        event_id=event_id,
        session_id=session_id,
        agent_id=agent_id,
        event_type=EventType.POLICY_VIOLATION,
        occurred_at=occurred_at,
        sequence=1,
        payload={
            "severity": "WARNING",
            "category": category,
            "action": "BLOCKED",
            "destination": f"cheat-{event_id}.org",
        },
        correlation_id=f"corr_{event_id}",
    )
    return GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.EVENT,
        message_id=event_id,
        correlation_id=event.correlation_id,
        source_id=agent_id,
        target_id="backend",
        payload=event.model_dump(mode="json"),
    )


class MockUplink:
    def __init__(self, status: PresenceHealth = PresenceHealth.ONLINE):
        self.status = status
        self.messages: list[GatewayEnvelope] = []
        self._outbound: asyncio.Queue[GatewayEnvelope] = asyncio.Queue(maxsize=1000)

    async def send(self, envelope: GatewayEnvelope) -> None:
        if self.status != PresenceHealth.ONLINE:
            raise OSError("Uplink is offline")
        self.messages.append(envelope)


class MockAgentSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.closed: list[tuple[int, str | None]] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed.append((code, reason))


def _make_flusher(
    buffer: EventBuffer,
    uplink: MockUplink,
    clock=lambda: datetime.now(UTC),
) -> EventFlusher:
    return EventFlusher(
        buffer,
        uplink,
        initial_delay_seconds=1.0,
        max_delay_seconds=8.0,
        jitter_ratio=0.0,
        batch_size=20,
        poll_interval_seconds=0.01,
        jitter=lambda: 0.0,
        clock=clock,
    )


def _setup_backend(db_path: Path):
    container = build_container(Settings(db_path))
    container.register_gateway(RegisterGatewayInput("GW-01", "LAB-01", "1.0.0"))
    container.register_agent(RegisterAgentInput("PC01", "HOST-1", "192.168.1.10", "1.1.0"))
    container.register_agent(RegisterAgentInput("PC02", "HOST-2", "192.168.1.11", "1.1.0"))
    container.bind_agent_to_gateway(BindAgentToGatewayInput("PC01", "GW-01"))
    container.bind_agent_to_gateway(BindAgentToGatewayInput("PC02", "GW-01"))
    session = ExamSession.create("Exam", "LAB-01", "GW-01", ["PC01", "PC02"])
    session.id = "SES-001"
    session.state = SessionState.RUNNING
    with container.database.unit_of_work() as uow:
        uow.sessions.add(session)
        uow.commit()
    return container


# TEST 1: Backend offline, Gateway buffer 10 Events rồi Backend online -> nhận đủ 10
def test_1_backend_offline_gateway_buffers_10_events_then_online_flushes_all_10(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gw-t1.db"
    buffer = EventBuffer(db_path, max_rows=20, degraded_threshold=15)
    uplink = MockUplink(status=PresenceHealth.OFFLINE)
    flusher = _make_flusher(buffer, uplink)
    connections = AgentConnectionManager()
    router = GatewayRouter("GW-01", connections, PresenceStore(), uplink, buffer, flusher)

    # 10 events sent while Backend offline
    for i in range(1, 11):
        asyncio.run(router.route_agent("PC01", _make_event(f"EVT-{i:03d}")))

    # Flusher runs while offline: nothing sent
    asyncio.run(flusher.flush_once())
    assert buffer.count(include_terminal=False) == 10
    assert len(uplink.messages) == 0

    # Backend comes online
    uplink.status = PresenceHealth.ONLINE
    backend = _setup_backend(tmp_path / "backend-t1.db")

    asyncio.run(flusher.flush_once())
    assert len(uplink.messages) == 10

    # Backend processes all 10 messages and returns receipts
    for msg in uplink.messages:
        receipt = asyncio.run(_handle_gateway_message("GW-01", msg, backend))
        assert receipt is not None
        asyncio.run(flusher.handle_receipt(receipt))

    assert buffer.count() == 0
    with backend.database.unit_of_work() as uow:
        telemetry = uow.telemetry.list_for_session("SES-001")
        assert len(telemetry) == 10


# TEST 2: Gateway restart khi có Events PENDING -> khôi phục đúng từ SQLite
def test_2_gateway_restart_with_pending_events_recovers_from_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "gw-t2.db"
    buffer1 = EventBuffer(db_path, max_rows=20, degraded_threshold=15)
    uplink1 = MockUplink(status=PresenceHealth.OFFLINE)
    connections1 = AgentConnectionManager()
    router1 = GatewayRouter("GW-01", connections1, PresenceStore(), uplink1, buffer1)

    for i in range(1, 4):
        asyncio.run(router1.route_agent("PC01", _make_event(f"EVT-{i:03d}")))

    assert buffer1.count(include_terminal=False) == 3

    # Simulate Gateway crash & restart
    del router1
    del buffer1

    buffer2 = EventBuffer(db_path, max_rows=20, degraded_threshold=15)
    uplink2 = MockUplink(status=PresenceHealth.ONLINE)
    flusher2 = _make_flusher(buffer2, uplink2)

    assert buffer2.count(include_terminal=False) == 3

    # Flusher flushes recovered events
    asyncio.run(flusher2.flush_once())
    assert len(uplink2.messages) == 3
    event_ids = [Event.model_validate(m.payload).event_id for m in uplink2.messages]
    assert event_ids == ["EVT-001", "EVT-002", "EVT-003"]


# TEST 3: Gateway restart khi Events đang IN_FLIGHT -> retry đúng, không mất Event
def test_3_gateway_restart_with_in_flight_events_resets_and_retries_immediately(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gw-t3.db"
    buffer1 = EventBuffer(db_path, max_rows=20, degraded_threshold=15)
    now = datetime.now(UTC)

    # Put event and mark in flight with future timer (30 seconds out)
    buffer1.put(_make_event("EVT-INFLIGHT"), now=now)
    buffer1.mark_in_flight("EVT-INFLIGHT", now + timedelta(seconds=30))

    # Before restart, event state is IN_FLIGHT
    due_before = buffer1.due(now + timedelta(seconds=1), limit=10)
    # Without startup recovery, it would not be due at now + 1s
    assert len(due_before) == 0

    # Gateway process crashes and restarts 1 second later
    del buffer1

    restart_time = now + timedelta(seconds=1)
    buffer2 = EventBuffer(db_path, max_rows=20, degraded_threshold=15)

    # Event must be reset to PENDING and due immediately on restart!
    due_after = buffer2.due(restart_time, limit=10)
    assert len(due_after) == 1
    assert due_after[0].event_id == "EVT-INFLIGHT"
    assert due_after[0].state == "PENDING"

    # Uplink sends and completes
    uplink2 = MockUplink(status=PresenceHealth.ONLINE)
    flusher2 = _make_flusher(buffer2, uplink2, clock=lambda: restart_time)
    asyncio.run(flusher2.flush_once())
    assert len(uplink2.messages) == 1


# TEST 4: Backend đã lưu Event nhưng EVENT_RECEIPT thất lạc -> retry cùng event_id
def test_4_lost_receipt_retries_same_event_id_idempotent_no_duplicate_telemetry(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gw-t4.db"
    buffer = EventBuffer(db_path, max_rows=10, degraded_threshold=8)
    uplink = MockUplink(status=PresenceHealth.ONLINE)
    clock_time = NOW
    flusher = _make_flusher(buffer, uplink, clock=lambda: clock_time)

    buffer.put(_make_event("EVT-DUP-01"), now=clock_time)
    backend = _setup_backend(tmp_path / "backend-t4.db")

    # First send
    asyncio.run(flusher.flush_once())
    assert len(uplink.messages) == 1
    receipt1 = asyncio.run(_handle_gateway_message("GW-01", uplink.messages[0], backend))
    assert receipt1 is not None
    assert EventReceipt.model_validate(receipt1.payload).status == EventReceiptStatus.ACCEPTED

    # RECEIPT 1 IS LOST! Gateway does not call handle_receipt
    assert buffer.count() == 1

    # Time advances; flusher retries with EXACT SAME event_id
    clock_time += timedelta(seconds=5)
    asyncio.run(flusher.flush_once())
    assert len(uplink.messages) == 2
    assert uplink.messages[0].payload["event_id"] == uplink.messages[1].payload["event_id"]

    # Backend receives retry
    receipt2 = asyncio.run(_handle_gateway_message("GW-01", uplink.messages[1], backend))
    assert receipt2 is not None
    # Backend recognizes duplicate and returns ALREADY_PROCESSED
    assert EventReceipt.model_validate(receipt2.payload).status == (
        EventReceiptStatus.ALREADY_PROCESSED
    )

    # Gateway handles receipt2 and clears buffer
    asyncio.run(flusher.handle_receipt(receipt2))
    assert buffer.count() == 0
    assert flusher.events_duplicate_accepted == 1

    # Verify backend has only 1 telemetry entry
    with backend.database.unit_of_work() as uow:
        telemetry = uow.telemetry.list_for_session("SES-001")
        assert len(telemetry) == 1


# TEST 5: Hai Agent cùng gửi vi phạm -> Incident được phân biệt theo workstation (P1-02)
def test_5_two_agents_simultaneous_violations_distinct_incidents_per_workstation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gw-t5.db"
    buffer = EventBuffer(db_path, max_rows=10, degraded_threshold=8)
    uplink = MockUplink(status=PresenceHealth.ONLINE)
    flusher = _make_flusher(buffer, uplink)
    connections = AgentConnectionManager()
    router = GatewayRouter("GW-01", connections, PresenceStore(), uplink, buffer, flusher)

    backend = _setup_backend(tmp_path / "backend-t5.db")

    # PC01 and PC02 report violation in same session with same category
    asyncio.run(
        router.route_agent("PC01", _make_event("EVT-PC01", agent_id="PC01", category="CHEAT_TOOL"))
    )
    asyncio.run(
        router.route_agent("PC02", _make_event("EVT-PC02", agent_id="PC02", category="CHEAT_TOOL"))
    )

    asyncio.run(flusher.flush_once())
    assert len(uplink.messages) == 2

    for msg in uplink.messages:
        receipt = asyncio.run(_handle_gateway_message("GW-01", msg, backend))
        asyncio.run(flusher.handle_receipt(receipt))

    with backend.database.unit_of_work() as uow:
        incidents = uow.incidents.list_for_session("SES-001")
        # Per P1-02, distinct workstations produce distinct incidents!
        assert len(incidents) == 2
        workstations = {inc.workstation_id for inc in incidents}
        assert workstations == {"PC01", "PC02"}


# TEST 6: EventBuffer đầy hoặc SQLite gặp lỗi -> không silently drop, báo failure/health rõ ràng
def test_6_event_buffer_full_and_sqlite_error_reports_degraded_and_closes_with_1013(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gw-t6.db"
    buffer = EventBuffer(db_path, max_rows=2, degraded_threshold=2)

    buffer.put(_make_event("EVT-01"), now=NOW)
    buffer.put(_make_event("EVT-02"), now=NOW)

    # 3rd event exceeds max_rows -> raises EventBufferFullError
    with pytest.raises(EventBufferFullError):
        buffer.put(_make_event("EVT-03"), now=NOW)

    health = buffer.health(NOW)
    assert health.buffer_status == PresenceHealth.DEGRADED
    assert health.last_flush_error == "event buffer is full"
    assert buffer.count() == 2

    # Verify EventBufferFullError is caught as backpressure
    assert issubclass(EventBufferFullError, OSError)


# TEST 7: Backend restart và Gateway reconnect -> Agent LAN connections/presence hoạt động đúng
def test_7_backend_restart_and_gateway_reconnect_preserves_agent_lan_and_announces_presence(
) -> None:
    async def scenario() -> None:
        connections = AgentConnectionManager()
        presence = PresenceStore()
        uplink = MockUplink(status=PresenceHealth.ONLINE)
        router = GatewayRouter("GW-01", connections, presence, uplink)

        socket1, socket2 = MockAgentSocket(), MockAgentSocket()
        await connections.connect("PC01", socket1)
        await connections.connect("PC02", socket2)

        hello1 = AgentHello(
            protocol_version=2,
            agent_id="PC01",
            hostname="HOST-1",
            ip_address="192.168.1.10",
            agent_version="1.1.0",
        )
        hello2 = AgentHello(
            protocol_version=2,
            agent_id="PC02",
            hostname="HOST-2",
            ip_address="192.168.1.11",
            agent_version="1.1.0",
        )
        await router.agent_connected(hello1)
        await router.agent_connected(hello2)

        # Backend drops connection
        uplink.status = PresenceHealth.OFFLINE
        uplink.messages.clear()

        # Agents stay connected on LAN!
        assert await connections.is_connected("PC01")
        assert await connections.is_connected("PC02")
        assert len(socket1.closed) == 0
        assert len(socket2.closed) == 0

        # Backend reconnects
        uplink.status = PresenceHealth.ONLINE
        await router.announce_current_agents()

        message_types = [m.message_type for m in uplink.messages]
        assert GatewayMessageType.AGENT_HELLO in message_types
        assert GatewayMessageType.PRESENCE in message_types
        assert any(m.source_id == "PC01" for m in uplink.messages)
        assert any(m.source_id == "PC02" for m in uplink.messages)

    asyncio.run(scenario())


# TEST 8: 30 Agents kết nối WSS, Backend gián đoạn rồi phục hồi -> không làm sai ACK và Events
def test_8_30_agents_connected_backend_interrupted_then_recovers_acks_and_events_intact(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "gw-t8.db"
        buffer = EventBuffer(db_path, max_rows=100, degraded_threshold=80)
        connections = AgentConnectionManager()
        presence = PresenceStore()
        uplink = MockUplink(status=PresenceHealth.OFFLINE)
        flusher = _make_flusher(buffer, uplink)
        router = GatewayRouter("GW-01", connections, presence, uplink, buffer, flusher)

        # 30 agents connect on LAN
        for i in range(1, 31):
            agent_id = f"PC{i:02d}"
            await connections.connect(agent_id, MockAgentSocket())

        # While Backend is OFFLINE, agents generate 10 Events and 5 ACKs
        for i in range(1, 11):
            agent_id = f"PC{i:02d}"
            await router.route_agent(agent_id, _make_event(f"EVT-{i:03d}", agent_id=agent_id))

        assert buffer.count(include_terminal=False) == 10

        # Backend recovers
        uplink.status = PresenceHealth.ONLINE
        await flusher.flush_once()

        assert len(uplink.messages) == 10
        received_ids = {Event.model_validate(m.payload).event_id for m in uplink.messages}
        expected_ids = {f"EVT-{i:03d}" for i in range(1, 11)}
        assert received_ids == expected_ids

    asyncio.run(scenario())


# TEST 9: Event bị Backend từ chối vĩnh viễn -> không retry vô hạn, có dấu vết để điều tra
def test_9_permanently_rejected_event_marks_terminal_no_infinite_retry_auditable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "gw-t9.db"
    buffer = EventBuffer(db_path, max_rows=10, degraded_threshold=8)
    uplink = MockUplink(status=PresenceHealth.ONLINE)
    now = NOW
    flusher = _make_flusher(buffer, uplink, clock=lambda: now)

    buffer.put(_make_event("EVT-REJECT"), now=now)
    asyncio.run(flusher.flush_once())
    assert len(uplink.messages) == 1

    # Backend sends terminal rejection (e.g. SESSION_MISMATCH)
    rejection = EventReceipt(
        protocol_version=2,
        event_id="EVT-REJECT",
        status=EventReceiptStatus.REJECTED,
        received_at=now,
        error_code=ErrorCode.SESSION_MISMATCH,
        error_message="Exam session does not exist",
    )
    rej_envelope = GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.EVENT_RECEIPT,
        message_id="receipt-reject",
        correlation_id="corr_EVT-REJECT",
        source_id="backend",
        target_id="GW-01",
        payload=rejection.model_dump(mode="json"),
    )
    asyncio.run(flusher.handle_receipt(rej_envelope))

    # Flusher must NOT retry this event
    now += timedelta(seconds=60)
    asyncio.run(flusher.flush_once())
    assert len(uplink.messages) == 1  # No second attempt

    # Audit trace and degraded health
    health = buffer.health(now)
    assert health.terminal_failed_count == 1
    assert health.buffer_status == PresenceHealth.DEGRADED
    assert "Exam session does not exist" in (health.last_flush_error or "")


# TEST 10: Agent nhận được EVENT_RECEIPT từ application.routing.GatewayRouter
def test_10_agent_receives_event_receipt_from_application_gateway_router(tmp_path: Path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "gw-t10.db"
        buffer = EventBuffer(db_path, max_rows=10, degraded_threshold=8)
        connections = AgentConnectionManager()
        socket = MockAgentSocket()
        await connections.connect("PC01", socket)

        uplink = MockUplink(status=PresenceHealth.ONLINE)
        router = GatewayRouter("GW-01", connections, PresenceStore(), uplink, buffer)

        # Agent sends EVENT
        await router.route_agent("PC01", _make_event("EVT-ACK-TEST", agent_id="PC01"))

        # Crucial check: socket MUST have received an EVENT_RECEIPT!
        assert len(socket.sent) == 1
        receipt_env = GatewayEnvelope.model_validate_json(socket.sent[0])
        assert receipt_env.message_type == GatewayMessageType.EVENT_RECEIPT
        receipt = EventReceipt.model_validate(receipt_env.payload)
        assert receipt.event_id == "EVT-ACK-TEST"
        assert receipt.status == EventReceiptStatus.ACCEPTED

    asyncio.run(scenario())
