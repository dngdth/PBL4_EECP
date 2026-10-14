from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.domain.interfaces.unit_of_work import UnitOfWorkFactory


class SecurityAuditService:
    """Append rate-limited security decisions to the existing audit hash chain."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        throttle_seconds: int = 30,
    ):
        self._uow_factory = uow_factory
        self._clock = clock
        self._throttle = timedelta(seconds=throttle_seconds)
        self._recent: dict[tuple[str, str, str], datetime] = {}
        self._lock = threading.Lock()

    def record(
        self,
        *,
        action: str,
        actor: str,
        actor_type: str,
        resource_type: str,
        resource_id: str,
        reason_code: str,
        session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> bool:
        now = self._clock()
        key = (action, actor, resource_id)
        with self._lock:
            previous = self._recent.get(key)
            if previous is not None and now - previous < self._throttle:
                return False
            self._recent[key] = now
        details = {
            "actor_type": actor_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "decision": "DENY",
            "reason_code": reason_code,
        }
        if correlation_id:
            details["correlation_id"] = correlation_id[:128]
        with self._uow_factory() as uow:
            uow.audits.append(
                session_id,
                actor=actor[:128] or "anonymous",
                action=action,
                target=resource_id[:128] or resource_type,
                details=details,
            )
            uow.commit()
        return True
