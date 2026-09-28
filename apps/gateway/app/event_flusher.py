from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Protocol

from apps.gateway.app.event_buffer import EventBuffer
from contracts.v2 import (
    EventReceipt,
    EventReceiptStatus,
    GatewayEnvelope,
    PresenceHealth,
)


class EventUplink(Protocol):
    status: PresenceHealth

    async def send(self, envelope: GatewayEnvelope) -> None: ...


class EventFlusher:
    """Single bounded Gateway worker for at-least-once Event delivery."""

    def __init__(
        self,
        buffer: EventBuffer,
        uplink: EventUplink,
        *,
        initial_delay_seconds: float,
        max_delay_seconds: float,
        jitter_ratio: float,
        batch_size: int,
        poll_interval_seconds: float,
        jitter: Callable[[], float] = random.random,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if initial_delay_seconds <= 0 or max_delay_seconds < initial_delay_seconds:
            raise ValueError("event retry delays are invalid")
        if not 0 <= jitter_ratio <= 1:
            raise ValueError("event retry jitter ratio must be between zero and one")
        if batch_size <= 0 or poll_interval_seconds <= 0:
            raise ValueError("event flush batch and interval must be positive")
        self._buffer = buffer
        self._uplink = uplink
        self._initial = initial_delay_seconds
        self._maximum = max_delay_seconds
        self._jitter_ratio = jitter_ratio
        self._batch_size = batch_size
        self._poll_interval = poll_interval_seconds
        self._jitter = jitter
        self._clock = clock
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.events_buffered = 0
        self.events_delivered = 0
        self.events_retried = 0
        self.events_duplicate_accepted = 0
        self.events_failed_terminal = 0

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="gateway-event-flusher")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    def note_buffered(self, inserted: bool) -> None:
        if inserted:
            self.events_buffered += 1

    async def handle_receipt(self, envelope: GatewayEnvelope) -> None:
        receipt = EventReceipt.model_validate(envelope.payload)
        if receipt.status in {
            EventReceiptStatus.ACCEPTED,
            EventReceiptStatus.ALREADY_PROCESSED,
        }:
            self._buffer.complete(receipt.event_id, receipt.received_at)
            self.events_delivered += 1
            if receipt.status == EventReceiptStatus.ALREADY_PROCESSED:
                self.events_duplicate_accepted += 1
        else:
            self._buffer.mark_terminal(
                receipt.event_id,
                receipt.error_message or str(receipt.error_code or "terminal rejection"),
            )
            self.events_failed_terminal += 1
        self._wake.set()

    async def flush_once(self) -> None:
        if self._uplink.status != PresenceHealth.ONLINE:
            return
        now = self._clock()
        for record in self._buffer.due(now, self._batch_size):
            attempt = record.attempt_count + 1
            exponent = min(attempt - 1, 30)
            delay = min(self._maximum, self._initial * (2**exponent))
            delay *= 1 + self._jitter_ratio * self._jitter()
            retry_at = now + timedelta(seconds=delay)
            self._buffer.mark_in_flight(record.event_id, retry_at)
            try:
                await self._uplink.send(record.envelope)
                if attempt > 1:
                    self.events_retried += 1
            except (OSError, asyncio.QueueFull) as exc:
                self._buffer.schedule_retry(record.event_id, retry_at, str(exc))
                self.events_retried += 1

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.flush_once()
            self._wake.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)
