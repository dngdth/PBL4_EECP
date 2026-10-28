from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from contracts.v2 import Event, GatewayEnvelope, GatewayMessageType


class EventSpoolFullError(ValueError):
    pass


class AgentEventSpool:
    """Bounded, SQLite WAL-backed write-ahead event spool on the Agent workstation."""

    def __init__(self, path: str | Path, *, max_rows: int = 1000):
        if max_rows <= 0:
            raise ValueError("event spool max rows must be positive")
        self.path = str(path)
        self.max_rows = max_rows
        self._lock = threading.Lock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    correlation_id TEXT,
                    envelope_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING'
                );
                CREATE INDEX IF NOT EXISTS ix_agent_events_seq
                    ON agent_events(sequence, created_at);
                CREATE TABLE IF NOT EXISTS agent_spool_meta (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(agent_events)").fetchall()
            }
            additions = {
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "next_attempt_at": "TEXT",
                "last_error_code": "TEXT",
                "last_error_message": "TEXT",
                "updated_at": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE agent_events ADD COLUMN {name} {definition}"
                    )
            connection.commit()

    def record(self, envelope: GatewayEnvelope, *, now: datetime | None = None) -> bool:
        if envelope.message_type != GatewayMessageType.EVENT:
            raise ValueError("only Event envelopes are stored in event spool")
        event = Event.model_validate(envelope.payload)
        at = now or datetime.now(UTC)
        encoded = envelope.to_json()
        with self._lock, closing(self._connect()) as connection:
            existing = connection.execute(
                "SELECT envelope_json FROM agent_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                if existing["envelope_json"] != encoded:
                    raise ValueError("event_id collision with different event content")
                return False
            count = connection.execute("SELECT COUNT(*) FROM agent_events").fetchone()[0]
            if count >= self.max_rows:
                raise EventSpoolFullError(
                    f"Agent event spool is full (capacity={self.max_rows})"
                )
            connection.execute(
                """
                INSERT INTO agent_events(
                    event_id, session_id, agent_id, event_type, occurred_at,
                    sequence, correlation_id, envelope_json, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
                """,
                (
                    event.event_id,
                    event.session_id,
                    event.agent_id,
                    event.event_type.value,
                    event.occurred_at.isoformat(),
                    event.sequence,
                    event.correlation_id,
                    encoded,
                    at.isoformat(),
                ),
            )
            if event.sequence is not None:
                connection.execute(
                    """
                    INSERT INTO agent_spool_meta(key, value) VALUES ('max_sequence', ?)
                    ON CONFLICT(key) DO UPDATE SET value = MAX(value, excluded.value)
                    """,
                    (event.sequence,),
                )
            connection.commit()
            return True

    def pending(
        self, limit: int = 100, *, now: datetime | None = None
    ) -> list[GatewayEnvelope]:
        at = now or datetime.now(UTC)
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT envelope_json FROM agent_events
                 WHERE status = 'PENDING'
                   AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                 ORDER BY sequence ASC, created_at ASC
                 LIMIT ?
                """,
                (at.isoformat(), limit),
            ).fetchall()
        return [GatewayEnvelope.model_validate_json(row["envelope_json"]) for row in rows]

    def complete(self, event_id: str) -> bool:
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM agent_events WHERE event_id = ?",
                (event_id,),
            )
            connection.commit()
            return cursor.rowcount > 0

    def reject(
        self,
        event_id: str,
        *,
        permanent: bool,
        error_code: str,
        error_message: str | None,
        now: datetime | None = None,
    ) -> bool:
        at = now or datetime.now(UTC)
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT attempt_count FROM agent_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                return False
            attempts = int(row["attempt_count"] or 0) + 1
            next_attempt = None
            status = "QUARANTINED" if permanent else "PENDING"
            if not permanent:
                delay = min(300, 2 ** min(attempts, 8))
                next_attempt = (at + timedelta(seconds=delay)).isoformat()
            connection.execute(
                """
                UPDATE agent_events
                   SET status = ?, attempt_count = ?, next_attempt_at = ?,
                       last_error_code = ?, last_error_message = ?, updated_at = ?
                 WHERE event_id = ?
                """,
                (
                    status,
                    attempts,
                    next_attempt,
                    error_code[:128],
                    (error_message or "")[:500] or None,
                    at.isoformat(),
                    event_id,
                ),
            )
            connection.commit()
            return True

    def count(self) -> int:
        with self._lock, closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM agent_events WHERE status = 'PENDING'"
                ).fetchone()[0]
            )

    def quarantined_count(self) -> int:
        with self._lock, closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM agent_events WHERE status = 'QUARANTINED'"
                ).fetchone()[0]
            )

    def max_sequence(self) -> int:
        with self._lock, closing(self._connect()) as connection:
            row_meta = connection.execute(
                "SELECT value FROM agent_spool_meta WHERE key = 'max_sequence'"
            ).fetchone()
            meta_max = int(row_meta[0]) if row_meta and row_meta[0] is not None else 0
            row_events = connection.execute("SELECT MAX(sequence) FROM agent_events").fetchone()
            events_max = int(row_events[0]) if row_events and row_events[0] is not None else 0
            return max(meta_max, events_max)

    def has(self, event_id: str) -> bool:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM agent_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return row is not None

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("DELETE FROM agent_events")
            connection.execute("DELETE FROM agent_spool_meta")
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
