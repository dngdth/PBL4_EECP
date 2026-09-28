from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agent.application.privileged_execution import PrivilegedExecutionPort
from contracts.v2 import (
    AckStatus,
    CommandType,
    HealthCheckPayload,
    ServiceHealth,
    ServiceRequest,
)


class ServiceHealthSensor:
    """Observe Service availability and active-policy integrity over Named Pipe."""

    def __init__(
        self,
        executor: PrivilegedExecutionPort,
        report_health: Callable[[str, str], None],
        report_integrity: Callable[[str, str, str | None], None],
        *,
        interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if interval_seconds <= 0:
            raise ValueError("Service health interval must be positive")
        self._executor = executor
        self._report_health = report_health
        self._report_integrity = report_integrity
        self._interval = interval_seconds
        self._clock = clock
        self._utc_clock = utc_clock
        self._session_id: str | None = None
        self._expected_hash: str | None = None
        self._next_check = 0.0
        self._available: bool | None = None
        self._mismatch: tuple[str, str | None] | None = None
        self.service_health = ServiceHealth.HEALTHY
        self.active_policy_hash: str | None = None

    def activate(self, session_id: str, payload: dict[str, Any]) -> None:
        expected = payload.get("policy_hash")
        expected_hash = expected if isinstance(expected, str) else None
        if self._session_id != session_id or self._expected_hash != expected_hash:
            self._next_check = 0.0
            self._mismatch = None
        self._session_id = session_id
        self._expected_hash = expected_hash

    def deactivate(self) -> None:
        self._session_id = None
        self._expected_hash = None
        self._mismatch = None
        self._available = None
        self.active_policy_hash = None
        self.service_health = ServiceHealth.HEALTHY

    def poll(self) -> None:
        session_id = self._session_id
        now = self._clock()
        if session_id is None or now < self._next_check:
            return
        self._next_check = now + self._interval
        token = uuid.uuid4().hex
        request = ServiceRequest(
            protocol_version=2,
            request_id=f"health_{token}",
            command_id=f"health_{token}",
            operation=CommandType.HEALTH_CHECK,
            session_id=session_id,
            payload=HealthCheckPayload(nonce=token),
            correlation_id=f"health_{token}",
        )
        result = self._executor.execute(request)
        if result.status != AckStatus.SUCCEEDED:
            self._record_unavailable(session_id)
            return

        was_unavailable = self._available is False
        self._available = True
        self.active_policy_hash = result.applied_hash
        self.service_health = ServiceHealth.HEALTHY
        if was_unavailable:
            self._report_health(session_id, "RECOVERED")

        expected = self._expected_hash
        mismatch = (
            (expected, result.applied_hash)
            if expected and expected != result.applied_hash
            else None
        )
        if mismatch is not None:
            self.service_health = ServiceHealth.DEGRADED
            if mismatch != self._mismatch:
                self._report_integrity(session_id, expected, result.applied_hash)
        self._mismatch = mismatch

    def _record_unavailable(self, session_id: str) -> None:
        if self._available is not False:
            self._report_health(session_id, "UNAVAILABLE")
        self._available = False
        self.active_policy_hash = None
        self.service_health = ServiceHealth.UNAVAILABLE
