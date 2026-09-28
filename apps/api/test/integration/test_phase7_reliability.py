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

from apps.gateway.app.connections import AgentConnectionManager
from apps.gateway.app.event_buffer import EventBuffer, EventBufferFullError
from apps.gateway.app.event_flusher import EventFlusher
from apps.gateway.app.presence import PresenceStore
from apps.gateway.app.routing import GatewayRouter
from contracts.v2 import (
    ErrorCode,
    Event,
    EventReceipt,
    EventReceiptStatus,
    EventType,
    GatewayEnvelope,
    GatewayMessageType,
    Presence,
    PresenceHealth,
)

NOW = datetime.now(UTC)


def _event(event_id: str = "EVT-001", *, occurred_at: datetime = NOW) -> GatewayEnvelope:
    event = Event(
        protocol_version=2,
        event_id=event_id,
        session_id="SES-001",
        agent_id="AGT-001",
        event_type=EventType.POLICY_VIOLATION,
        occurred_at=occurred_at,
        sequence=1,
        payload={
            "severity": "WARNING",
            "category": "PROHIBITED_WEBSITE",
            "action": "BLOCKED",
            "destination": "example.invalid",
        },
        correlation_id=f"CORR-{event_id}",
    )
    return GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.EVENT,
        message_id=event_id,
        correlation_id=event.correlation_id,
        source_id=event.agent_id,
        target_id="backend",
        payload=event.model_dump(mode="json"),
    )


def _receipt(event_id: str, status: EventReceiptStatus) -> GatewayEnvelope:
    receipt = EventReceipt(
        protocol_version=2,
        event_id=event_id,
        status=status,
        received_at=NOW,
        error_code=(ErrorCode.SESSION_MISMATCH if status == EventReceiptStatus.REJECTED else None),
        error_message=("terminal rejection" if status == EventReceiptStatus.REJECTED else None),
    )
    return GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.EVENT_RECEIPT,
        message_id=f"receipt-{event_id}",
        correlation_id=f"CORR-{event_id}",
        source_id="backend",
        target_id="GW-A",
        payload=receipt.model_dump(mode="json"),
    )


class Uplink:
    def __init__(self, status=PresenceHealth.OFFLINE):
        self.status = status
        self.messages: list[GatewayEnvelope] = []
        self.fail_next = False

    async def send(self, envelope):
        if self.fail_next:
            self.fail_next = False
            raise OSError("temporary Backend failure")
        self.messages.append(envelope)


def _flusher(
    buffer: EventBuffer,
    uplink: Uplink,
    clock=lambda: datetime.now(UTC),
) -> EventFlusher:
    return EventFlusher(
        buffer,
        uplink,
        initial_delay_seconds=1,
        max_delay_seconds=8,
        jitter_ratio=0,
        batch_size=10,
        poll_interval_seconds=0.01,
        jitter=lambda: 0,
        clock=clock,
    )


def test_gateway_buffer_survives_restart_and_flushes_after_wan_recovery(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-events.db"
    first = EventBuffer(path, max_rows=10, degraded_threshold=8)
    offline = Uplink()
    first_flusher = _flusher(first, offline)
    router = GatewayRouter(
        "GW-A",
        AgentConnectionManager(),
        PresenceStore(),
        offline,
        first,
        first_flusher,
    )

    asyncio.run(router.route_agent("AGT-001", _event()))
    asyncio.run(first_flusher.flush_once())
    assert first.count(include_terminal=False) == 1
    assert offline.messages == []

    restarted = EventBuffer(path, max_rows=10, degraded_threshold=8)
    online = Uplink(PresenceHealth.ONLINE)
    restarted_flusher = _flusher(restarted, online)
    asyncio.run(restarted_flusher.flush_once())
    assert [Event.model_validate(item.payload).event_id for item in online.messages] == [
        "EVT-001"
    ]
    backend = _backend(tmp_path / "wan-backend.db")
    receipt = asyncio.run(
        _handle_gateway_message("GW-A", online.messages[0], backend)
    )
    assert receipt is not None
    asyncio.run(restarted_flusher.handle_receipt(receipt))
    assert restarted.count() == 0
    with backend.database.unit_of_work() as uow:
        assert [item.id for item in uow.telemetry.list_for_session("SES-001")] == [
            "EVT-001"
        ]


def test_background_flusher_wakes_and_sends_after_uplink_recovery(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        buffer = EventBuffer(
            tmp_path / "automatic-flush.db",
            max_rows=10,
            degraded_threshold=8,
        )
        buffer.put(_event(), now=NOW)
        uplink = Uplink()
        flusher = _flusher(buffer, uplink, clock=lambda: NOW)
        await flusher.start()
        try:
            await asyncio.sleep(0.02)
            assert uplink.messages == []
            uplink.status = PresenceHealth.ONLINE
            flusher.wake()
            for _ in range(20):
                if uplink.messages:
                    break
                await asyncio.sleep(0.01)
            assert Event.model_validate(uplink.messages[0].payload).event_id == "EVT-001"
        finally:
            await flusher.stop()

    asyncio.run(scenario())


def test_lost_receipt_retries_same_event_id_and_already_processed_completes(
    tmp_path: Path,
) -> None:
    now = NOW
    buffer = EventBuffer(tmp_path / "lost-receipt.db", max_rows=10, degraded_threshold=8)
    buffer.put(_event(), now=now)
    uplink = Uplink(PresenceHealth.ONLINE)
    flusher = _flusher(buffer, uplink, clock=lambda: now)

    asyncio.run(flusher.flush_once())
    now += timedelta(seconds=2)
    asyncio.run(flusher.flush_once())

    ids = [Event.model_validate(item.payload).event_id for item in uplink.messages]
    assert ids == ["EVT-001", "EVT-001"]
    asyncio.run(
        flusher.handle_receipt(
            _receipt("EVT-001", EventReceiptStatus.ALREADY_PROCESSED)
        )
    )
    assert buffer.count() == 0
    assert flusher.events_duplicate_accepted == 1


def test_temporary_failure_keeps_event_and_terminal_reject_stops_retry(
    tmp_path: Path,
) -> None:
    now = NOW
    buffer = EventBuffer(tmp_path / "failures.db", max_rows=10, degraded_threshold=8)
    buffer.put(_event(), now=now)
    uplink = Uplink(PresenceHealth.ONLINE)
    uplink.fail_next = True
    flusher = _flusher(buffer, uplink, clock=lambda: now)

    asyncio.run(flusher.flush_once())
    assert buffer.count(include_terminal=False) == 1
    now += timedelta(seconds=2)
    asyncio.run(flusher.flush_once())
    assert len(uplink.messages) == 1
    asyncio.run(flusher.handle_receipt(_receipt("EVT-001", EventReceiptStatus.REJECTED)))
    now += timedelta(seconds=20)
    asyncio.run(flusher.flush_once())
    assert len(uplink.messages) == 1
    assert buffer.health(now).terminal_failed_count == 1


def test_buffer_full_is_visible_and_never_silently_drops_existing_event(
    tmp_path: Path,
) -> None:
    buffer = EventBuffer(tmp_path / "full.db", max_rows=1, degraded_threshold=1)
    buffer.put(_event("EVT-001"), now=NOW)

    with pytest.raises(EventBufferFullError):
        buffer.put(_event("EVT-002"), now=NOW)

    health = buffer.health(NOW)
    assert buffer.count() == 1
    assert health.pending_event_count == 1
    assert health.buffer_status == PresenceHealth.DEGRADED
    assert health.last_flush_error == "event buffer is full"


def _backend(path: Path):
    container = build_container(Settings(path))
    container.register_gateway(RegisterGatewayInput("GW-A", "LAB-A", "1.0.0"))
    container.register_agent(
        RegisterAgentInput("AGT-001", "HOST", "192.0.2.1", "1.1.0")
    )
    container.bind_agent_to_gateway(BindAgentToGatewayInput("AGT-001", "GW-A"))
    session = ExamSession.create("Exam", "LAB-A", "GW-A", ["AGT-001"])
    session.id = "SES-001"
    session.state = SessionState.RUNNING
    with container.database.unit_of_work() as uow:
        uow.sessions.add(session)
        uow.commit()
    return container


def test_backend_persistent_dedupe_survives_restart_without_duplicate_incident(
    tmp_path: Path,
) -> None:
    path = tmp_path / "backend.db"
    first = _backend(path)
    accepted = asyncio.run(_handle_gateway_message("GW-A", _event(), first))
    assert accepted is not None
    assert EventReceipt.model_validate(accepted.payload).status == EventReceiptStatus.ACCEPTED

    restarted = build_container(Settings(path))
    duplicate = asyncio.run(_handle_gateway_message("GW-A", _event(), restarted))
    assert duplicate is not None
    assert EventReceipt.model_validate(duplicate.payload).status == (
        EventReceiptStatus.ALREADY_PROCESSED
    )
    with restarted.database.unit_of_work() as uow:
        events = uow.telemetry.list_for_session("SES-001")
        incidents = uow.incidents.list_for_session("SES-001")
        audits = uow.audits.list_for_session("SES-001")
    assert [event.id for event in events] == ["EVT-001"]
    assert len(incidents) == 1
    assert sum(event.action == "INCIDENT_CREATED" for event in audits) == 1


def test_backend_preserves_out_of_order_occurrence_time(tmp_path: Path) -> None:
    container = _backend(tmp_path / "out-of-order.db")
    later = _event("EVT-LATER", occurred_at=NOW + timedelta(minutes=1))
    earlier = _event("EVT-EARLIER", occurred_at=NOW - timedelta(minutes=1))

    asyncio.run(_handle_gateway_message("GW-A", later, container))
    asyncio.run(_handle_gateway_message("GW-A", earlier, container))

    with container.database.unit_of_work() as uow:
        events = uow.telemetry.list_for_session("SES-001")
    assert [event.id for event in events] == ["EVT-EARLIER", "EVT-LATER"]
    assert events[0].occurred_at == NOW - timedelta(minutes=1)


def test_presence_transitions_online_degraded_offline_and_reconnect() -> None:
    async def scenario() -> None:
        connections = AgentConnectionManager()
        presence = PresenceStore()
        uplink = Uplink(PresenceHealth.ONLINE)
        router = GatewayRouter("GW-A", connections, presence, uplink)

        class Socket:
            async def send_text(self, _data):
                return

            async def close(self, code=1000, reason=None):
                return

        socket = Socket()
        await connections.connect("AGT-001", socket)
        await presence.put(
            Presence(
                protocol_version=2,
                agent_id="AGT-001",
                gateway_id="GW-A",
                last_seen=NOW,
                health=PresenceHealth.ONLINE,
            )
        )
        connections._last_seen["AGT-001"] = datetime.now(UTC) - timedelta(seconds=20)
        await router.refresh_presence(10, 30)
        assert (await presence.get("AGT-001")).health == PresenceHealth.DEGRADED

        connections._last_seen["AGT-001"] = datetime.now(UTC) - timedelta(seconds=40)
        await router.refresh_presence(10, 30)
        assert (await presence.get("AGT-001")).health == PresenceHealth.OFFLINE

        await router._report_presence("AGT-001", PresenceHealth.ONLINE)
        assert (await presence.get("AGT-001")).health == PresenceHealth.ONLINE

    asyncio.run(scenario())
