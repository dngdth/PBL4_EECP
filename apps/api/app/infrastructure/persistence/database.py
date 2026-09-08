from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from app.domain.services.policy_profiles import BUILT_IN_POLICY_PROFILES
from app.domain.value_objects.primitives import canonical_json

if TYPE_CHECKING:
    from app.infrastructure.repositories.sqlite import SqliteUnitOfWork


SCHEMA = """
CREATE TABLE IF NOT EXISTS exam_sessions (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    status TEXT NOT NULL,
    agent_version TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agents_status_last_seen ON agents(status, last_seen);

CREATE TABLE IF NOT EXISTS session_workstations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    UNIQUE(session_id, agent_id),
    FOREIGN KEY(session_id) REFERENCES exam_sessions(id),
    FOREIGN KEY(agent_id) REFERENCES agents(id)
);
CREATE INDEX IF NOT EXISTS ix_session_workstations_session
    ON session_workstations(session_id, assigned_at, id);

CREATE TABLE IF NOT EXISTS commands (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT,
    error TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_retry_at TEXT,
    expires_at TEXT,
    FOREIGN KEY(session_id) REFERENCES exam_sessions(id)
);
CREATE INDEX IF NOT EXISTS ix_commands_target_status ON commands(target_id, status);

CREATE TABLE IF NOT EXISTS policy_profiles (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    rules TEXT NOT NULL,
    is_builtin INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    workstation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    destination TEXT,
    correlation_id TEXT,
    payload TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES exam_sessions(id)
);
CREATE INDEX IF NOT EXISTS ix_telemetry_session ON telemetry_events(session_id, occurred_at);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    workstation_id TEXT,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES exam_sessions(id)
);
CREATE INDEX IF NOT EXISTS ix_incidents_session ON incidents(session_id, created_at);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    session_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    details TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    chain_hash TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES exam_sessions(id)
);
CREATE INDEX IF NOT EXISTS ix_audit_session ON audit_events(session_id, sequence);
"""


class SqliteDatabase:
    def __init__(self, path: str | Path):
        self.path = str(path)

    def initialize(self) -> None:
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_commands(connection)
            self._seed_policy_profiles(connection)
            connection.commit()

    @staticmethod
    def _migrate_commands(connection: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(commands)").fetchall()
        }
        additions = {
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "last_attempt_at": "TEXT",
            "next_retry_at": "TEXT",
            "expires_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE commands ADD COLUMN {name} {definition}"
                )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_commands_delivery
                ON commands(target_id, status, next_retry_at)
            """
        )

    @staticmethod
    def _seed_policy_profiles(connection: sqlite3.Connection) -> None:
        connection.executemany(
            """
            INSERT OR IGNORE INTO policy_profiles(
                id, label, description, rules, is_builtin
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    profile.id,
                    profile.label,
                    profile.description,
                    canonical_json(profile.rules),
                    int(profile.is_builtin),
                )
                for profile in BUILT_IN_POLICY_PROFILES.list_all()
            ],
        )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def unit_of_work(self) -> SqliteUnitOfWork:
        from app.infrastructure.repositories.sqlite import SqliteUnitOfWork

        return SqliteUnitOfWork(self)
