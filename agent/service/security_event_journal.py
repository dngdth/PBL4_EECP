from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def protect_service_data_directory(path: Path) -> None:
    """Remove inherited access; only LocalSystem and Administrators own Service data."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        return
    result = subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise OSError("cannot protect EECP Service data directory ACL")


@dataclass(frozen=True, slots=True)
class SecurityEventRecord:
    event_id: str
    session_id: str | None
    event_type: str
    category: str
    action: str
    severity: str
    source: str
    occurred_at: datetime
    correlation_id: str
    details: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        session_id: str | None,
        event_type: str,
        category: str,
        action: str,
        severity: str,
        source: str,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> SecurityEventRecord:
        occurred_at = clock()
        event_id = f"evt_{uuid.uuid4().hex}"
        return cls(
            event_id=event_id,
            session_id=session_id,
            event_type=event_type,
            category=category,
            action=action,
            severity=severity,
            source=source,
            occurred_at=occurred_at,
            correlation_id=correlation_id or event_id,
            details=details or {},
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["occurred_at"] = self.occurred_at.isoformat()
        return value


class SecurityEventJournal:
    """Bounded, durable Service-owned journal; enforcement never depends on delivery."""

    def __init__(
        self,
        path: Path,
        *,
        max_rows: int = 10_000,
        retention_days: int = 14,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if max_rows < 1 or retention_days < 1:
            raise ValueError("security event journal limits must be positive")
        self.path = path
        self.max_rows = max_rows
        self.retention_days = retention_days
        self._clock = clock
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS security_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    session_id TEXT,
                    occurred_at TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    acknowledged_at TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_security_events_pending
                ON security_events(acknowledged_at, sequence);
                """
            )
            connection.commit()

    def append(self, event: SecurityEventRecord) -> bool:
        encoded = json.dumps(event.to_dict(), separators=(",", ":"), sort_keys=True)
        with self._lock, closing(self._connect()) as connection:
            self._prune(connection)
            count = connection.execute(
                "SELECT COUNT(*) FROM security_events WHERE acknowledged_at IS NULL"
            ).fetchone()[0]
            if count >= self.max_rows:
                raise OSError("security event journal is full")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO security_events(
                    event_id, session_id, occurred_at, event_json
                ) VALUES (?, ?, ?, ?)
                """,
                (event.event_id, event.session_id, event.occurred_at.isoformat(), encoded),
            )
            connection.commit()
            return cursor.rowcount > 0

    def pending(
        self, *, session_id: str, after_sequence: int = 0, limit: int = 100
    ) -> tuple[tuple[int, SecurityEventRecord], ...]:
        bounded_limit = max(1, min(limit, 100))
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT sequence, event_json FROM security_events
                WHERE acknowledged_at IS NULL AND session_id = ? AND sequence > ?
                ORDER BY sequence ASC LIMIT ?
                """,
                (session_id, max(0, after_sequence), bounded_limit),
            ).fetchall()
        return tuple(
            (int(row["sequence"]), _decode_event(row["event_json"])) for row in rows
        )

    def pending_sessions(self, *, limit: int = 100) -> tuple[str, ...]:
        """Return only session identifiers that still have unacknowledged events."""
        bounded_limit = max(1, min(limit, 100))
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT session_id, MIN(sequence) AS first_sequence
                FROM security_events
                WHERE acknowledged_at IS NULL AND session_id IS NOT NULL
                GROUP BY session_id
                ORDER BY first_sequence ASC LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return tuple(str(row["session_id"]) for row in rows)

    def acknowledge(self, session_id: str, event_ids: tuple[str, ...]) -> int:
        if not event_ids:
            return 0
        if len(event_ids) > 100:
            raise ValueError("at most 100 security events can be acknowledged")
        placeholders = ",".join("?" for _ in event_ids)
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                f"UPDATE security_events SET acknowledged_at = ? "
                f"WHERE acknowledged_at IS NULL AND session_id = ? "
                f"AND event_id IN ({placeholders})",
                (self._clock().isoformat(), session_id, *event_ids),
            )
            connection.commit()
            return cursor.rowcount

    def _prune(self, connection: sqlite3.Connection) -> None:
        cutoff = (self._clock() - timedelta(days=self.retention_days)).isoformat()
        connection.execute(
            "DELETE FROM security_events WHERE acknowledged_at IS NOT NULL "
            "AND occurred_at < ?",
            (cutoff,),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


def _decode_event(value: str) -> SecurityEventRecord:
    raw = json.loads(value)
    raw["occurred_at"] = datetime.fromisoformat(raw["occurred_at"])
    return SecurityEventRecord(**raw)
