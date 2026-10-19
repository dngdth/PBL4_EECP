from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
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
            count = connection.execute(
                "SELECT COUNT(*) FROM agent_events WHERE status = 'PENDING'"
            ).fetchone()[0]
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

    def pending(self, limit: int = 100) -> list[GatewayEnvelope]:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT envelope_json FROM agent_events
                 WHERE status = 'PENDING'
                 ORDER BY sequence ASC, created_at ASC
                 LIMIT ?
                """,
                (limit,),
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

    def count(self) -> int:
        with self._lock, closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM agent_events WHERE status = 'PENDING'"
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
