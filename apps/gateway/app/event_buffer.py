from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from contracts.v2 import Event, GatewayEnvelope, GatewayMessageType, PresenceHealth


class EventBufferFullError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BufferedEvent:
    event_id: str
    envelope: GatewayEnvelope
    state: str
    attempt_count: int
    next_attempt_at: datetime
    last_error: str | None


@dataclass(frozen=True, slots=True)
class EventBufferHealth:
    pending_event_count: int
    oldest_pending_event_age: float | None
    last_flush_success_at: datetime | None
    last_flush_error: str | None
    buffer_status: PresenceHealth
    terminal_failed_count: int


class EventBuffer:
    """Gateway-local SQLite write-ahead store for important Agent events."""

    def __init__(self, path: str | Path, *, max_rows: int, degraded_threshold: int):
        if max_rows <= 0:
            raise ValueError("event buffer max rows must be positive")
        if not 0 < degraded_threshold <= max_rows:
            raise ValueError("event buffer degraded threshold must be within max rows")
        self.path = str(path)
        self.max_rows = max_rows
        self.degraded_threshold = degraded_threshold
        self._lock = threading.Lock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS gateway_events (
                    event_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    sequence INTEGER,
                    correlation_id TEXT,
                    envelope_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_gateway_events_due
                    ON gateway_events(state, next_attempt_at, created_at);
                CREATE TABLE IF NOT EXISTS gateway_event_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            connection.commit()

    def put(self, envelope: GatewayEnvelope, *, now: datetime | None = None) -> bool:
        if envelope.message_type != GatewayMessageType.EVENT:
            raise ValueError("only Event envelopes are durable")
        event = Event.model_validate(envelope.payload)
        at = now or datetime.now(UTC)
        encoded = envelope.to_json()
        with self._lock, closing(self._connect()) as connection:
            existing = connection.execute(
                "SELECT envelope_json FROM gateway_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                if existing["envelope_json"] != encoded:
                    raise ValueError("event_id collision with different event content")
                return False
            count = connection.execute("SELECT COUNT(*) FROM gateway_events").fetchone()[0]
            if count >= self.max_rows:
                self._set_meta(connection, "last_flush_error", "event buffer is full")
                connection.commit()
                raise EventBufferFullError("Gateway event buffer is full")
            connection.execute(
                """
                INSERT INTO gateway_events(
                    event_id, agent_id, session_id, event_type, occurred_at,
                    sequence, correlation_id, envelope_json, state, attempt_count,
                    created_at, next_attempt_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, NULL)
                """,
                (
                    event.event_id,
                    event.agent_id,
                    event.session_id,
                    event.event_type.value,
                    event.occurred_at.isoformat(),
                    event.sequence,
                    event.correlation_id,
                    encoded,
                    at.isoformat(),
                    at.isoformat(),
                ),
            )
            connection.commit()
            return True

    def due(self, now: datetime, limit: int) -> tuple[BufferedEvent, ...]:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM gateway_events
                 WHERE state IN ('PENDING', 'IN_FLIGHT') AND next_attempt_at <= ?
                 ORDER BY next_attempt_at, created_at, event_id
                 LIMIT ?
                """,
                (now.isoformat(), limit),
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def mark_in_flight(self, event_id: str, next_attempt_at: datetime) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE gateway_events
                   SET state = 'IN_FLIGHT', attempt_count = attempt_count + 1,
                       next_attempt_at = ?, last_error = NULL
                 WHERE event_id = ? AND state IN ('PENDING', 'IN_FLIGHT')
                """,
                (next_attempt_at.isoformat(), event_id),
            )
            connection.commit()

    def schedule_retry(
        self, event_id: str, next_attempt_at: datetime, error: str
    ) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE gateway_events
                   SET state = 'PENDING', next_attempt_at = ?, last_error = ?
                 WHERE event_id = ?
                """,
                (next_attempt_at.isoformat(), error[:500], event_id),
            )
            self._set_meta(connection, "last_flush_error", error[:500])
            connection.commit()

    def complete(self, event_id: str, at: datetime) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("DELETE FROM gateway_events WHERE event_id = ?", (event_id,))
            self._set_meta(connection, "last_flush_success_at", at.isoformat())
            self._set_meta(connection, "last_flush_error", None)
            connection.commit()

    def mark_terminal(self, event_id: str, error: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE gateway_events
                   SET state = 'FAILED_TERMINAL', last_error = ?
                 WHERE event_id = ?
                """,
                (error[:500], event_id),
            )
            self._set_meta(connection, "last_flush_error", error[:500])
            connection.commit()

    def count(self, *, include_terminal: bool = True) -> int:
        query = "SELECT COUNT(*) FROM gateway_events"
        if not include_terminal:
            query += " WHERE state IN ('PENDING', 'IN_FLIGHT')"
        with self._lock, closing(self._connect()) as connection:
            return int(connection.execute(query).fetchone()[0])

    def health(self, now: datetime | None = None) -> EventBufferHealth:
        at = now or datetime.now(UTC)
        with self._lock, closing(self._connect()) as connection:
            pending, oldest = connection.execute(
                """
                SELECT COUNT(*), MIN(created_at) FROM gateway_events
                 WHERE state IN ('PENDING', 'IN_FLIGHT')
                """
            ).fetchone()
            terminal = connection.execute(
                "SELECT COUNT(*) FROM gateway_events WHERE state = 'FAILED_TERMINAL'"
            ).fetchone()[0]
            total = connection.execute("SELECT COUNT(*) FROM gateway_events").fetchone()[0]
            success = self._get_meta(connection, "last_flush_success_at")
            error = self._get_meta(connection, "last_flush_error")
        oldest_at = datetime.fromisoformat(oldest) if oldest else None
        age = max(0.0, (at - oldest_at).total_seconds()) if oldest_at else None
        degraded = total >= self.degraded_threshold or terminal > 0
        return EventBufferHealth(
            pending_event_count=int(pending),
            oldest_pending_event_age=age,
            last_flush_success_at=datetime.fromisoformat(success) if success else None,
            last_flush_error=error,
            buffer_status=(PresenceHealth.DEGRADED if degraded else PresenceHealth.ONLINE),
            terminal_failed_count=int(terminal),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: str | None) -> None:
        connection.execute(
            """
            INSERT INTO gateway_event_meta(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    @staticmethod
    def _get_meta(connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute(
            "SELECT value FROM gateway_event_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> BufferedEvent:
        return BufferedEvent(
            event_id=row["event_id"],
            envelope=GatewayEnvelope.model_validate_json(row["envelope_json"]),
            state=row["state"],
            attempt_count=int(row["attempt_count"]),
            next_attempt_at=datetime.fromisoformat(row["next_attempt_at"]),
            last_error=row["last_error"],
        )
