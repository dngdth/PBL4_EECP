from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from app.domain.entities.agent import Agent
from app.domain.entities.exam_session import ExamSession
from app.domain.entities.operations import AuditEvent, Command, Incident, TelemetryEvent
from app.domain.entities.session_workstation import SessionWorkstation
from app.domain.exceptions.errors import ConcurrencyError, EntityNotFoundError
from app.domain.services.policies import compute_audit_hash
from app.domain.services.policy_profiles import PolicyProfileDefinition
from app.domain.value_objects.enums import (
    AgentStatus,
    CommandStatus,
    CommandType,
    IncidentStatus,
    Severity,
)
from app.domain.value_objects.primitives import canonical_json, utc_now
from app.infrastructure.persistence.database import SqliteDatabase


class SqliteAgentRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def add(self, agent: Agent) -> None:
        self._connection.execute(
            """
            INSERT INTO agents(
                id, hostname, ip_address, status, agent_version, last_seen, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            self._values(agent),
        )

    def get(self, agent_id: str) -> Agent:
        agent = self.find(agent_id)
        if agent is None:
            raise EntityNotFoundError(f"agent not found: {agent_id}")
        return agent

    def find(self, agent_id: str) -> Agent | None:
        row = self._connection.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return self._from_row(row) if row is not None else None

    def save(self, agent: Agent) -> None:
        cursor = self._connection.execute(
            """
            UPDATE agents
               SET hostname = ?, ip_address = ?, status = ?, agent_version = ?,
                   last_seen = ?, created_at = ?
             WHERE id = ?
            """,
            (
                agent.hostname,
                agent.ip_address,
                agent.status.value,
                agent.agent_version,
                agent.last_seen.isoformat(),
                agent.created_at.isoformat(),
                agent.id,
            ),
        )
        if cursor.rowcount != 1:
            raise EntityNotFoundError(f"agent not found: {agent.id}")

    def list_all(self) -> list[Agent]:
        rows = self._connection.execute("SELECT * FROM agents ORDER BY id").fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _values(agent: Agent) -> tuple:
        return (
            agent.id,
            agent.hostname,
            agent.ip_address,
            agent.status.value,
            agent.agent_version,
            agent.last_seen.isoformat(),
            agent.created_at.isoformat(),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Agent:
        return Agent(
            id=row["id"],
            hostname=row["hostname"],
            ip_address=row["ip_address"],
            status=AgentStatus(row["status"]),
            agent_version=row["agent_version"],
            last_seen=datetime.fromisoformat(row["last_seen"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteSessionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def add(self, session: ExamSession) -> None:
        self._connection.execute(
            "INSERT INTO exam_sessions(id, payload, version) VALUES (?, ?, ?)",
            (session.id, canonical_json(session.to_dict()), session.aggregate_version),
        )

    def get(self, session_id: str) -> ExamSession:
        row = self._connection.execute(
            "SELECT payload FROM exam_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise EntityNotFoundError(f"session not found: {session_id}")
        return ExamSession.from_dict(json.loads(row["payload"]))

    def save(self, session: ExamSession) -> None:
        expected_version = session.aggregate_version
        session.aggregate_version += 1
        cursor = self._connection.execute(
            """
            UPDATE exam_sessions
               SET payload = ?, version = ?
             WHERE id = ? AND version = ?
            """,
            (
                canonical_json(session.to_dict()),
                session.aggregate_version,
                session.id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            session.aggregate_version = expected_version
            raise ConcurrencyError(f"session was modified concurrently: {session.id}")

    def list_all(self) -> list[ExamSession]:
        rows = self._connection.execute(
            "SELECT payload FROM exam_sessions ORDER BY rowid DESC"
        ).fetchall()
        return [ExamSession.from_dict(json.loads(row["payload"])) for row in rows]


class SqliteSessionWorkstationRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def assign(self, assignment: SessionWorkstation) -> None:
        self._connection.execute(
            """
            INSERT INTO session_workstations(id, session_id, agent_id, assigned_at)
            VALUES (?, ?, ?, ?)
            """,
            self._values(assignment),
        )

    def assign_many(self, assignments: Sequence[SessionWorkstation]) -> None:
        self._connection.executemany(
            """
            INSERT INTO session_workstations(id, session_id, agent_id, assigned_at)
            VALUES (?, ?, ?, ?)
            """,
            [self._values(assignment) for assignment in assignments],
        )

    def list_for_session(self, session_id: str) -> list[SessionWorkstation]:
        rows = self._connection.execute(
            """
            SELECT id, session_id, agent_id, assigned_at
            FROM session_workstations
            WHERE session_id = ?
            ORDER BY assigned_at, id
            """,
            (session_id,),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _values(assignment: SessionWorkstation) -> tuple:
        return (
            assignment.id,
            assignment.session_id,
            assignment.agent_id,
            assignment.assigned_at.isoformat(),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SessionWorkstation:
        return SessionWorkstation(
            id=row["id"],
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            assigned_at=datetime.fromisoformat(row["assigned_at"]),
        )


class SqliteCommandRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def add_many(self, commands: Sequence[Command]) -> None:
        self._connection.executemany(
            """
            INSERT INTO commands(
                id, session_id, target_id, type, payload, status, created_at,
                acknowledged_at, error, attempt_count, last_attempt_at,
                next_retry_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [self._values(command) for command in commands],
        )

    def get(self, command_id: str) -> Command:
        row = self._connection.execute(
            "SELECT * FROM commands WHERE id = ?", (command_id,)
        ).fetchone()
        if row is None:
            raise EntityNotFoundError(f"command not found: {command_id}")
        return self._from_row(row)

    def save(self, command: Command) -> None:
        cursor = self._connection.execute(
            """
            UPDATE commands
               SET status = ?, acknowledged_at = ?, error = ?, attempt_count = ?,
                   last_attempt_at = ?, next_retry_at = ?, expires_at = ?
             WHERE id = ?
            """,
            (
                command.status.value,
                _iso(command.acknowledged_at),
                command.error,
                command.attempt_count,
                _iso(command.last_attempt_at),
                _iso(command.next_retry_at),
                _iso(command.expires_at),
                command.id,
            ),
        )
        if cursor.rowcount != 1:
            raise EntityNotFoundError(f"command not found: {command.id}")

    def available_for_target(self, target_id: str, at: datetime) -> list[Command]:
        rows = self._connection.execute(
            """
            SELECT * FROM commands
             WHERE target_id = ?
               AND (
                    status = ?
                    OR (status = ? AND next_retry_at <= ?)
               )
             ORDER BY created_at, id
            """,
            (
                target_id,
                CommandStatus.PENDING.value,
                CommandStatus.DELIVERED.value,
                at.isoformat(),
            ),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _values(command: Command) -> tuple:
        return (
            command.id,
            command.session_id,
            command.target_id,
            command.type.value,
            canonical_json(command.payload),
            command.status.value,
            command.created_at.isoformat(),
            _iso(command.acknowledged_at),
            command.error,
            command.attempt_count,
            _iso(command.last_attempt_at),
            _iso(command.next_retry_at),
            _iso(command.expires_at),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Command:
        return Command(
            id=row["id"],
            session_id=row["session_id"],
            target_id=row["target_id"],
            type=CommandType(row["type"]),
            payload=json.loads(row["payload"]),
            status=CommandStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            acknowledged_at=_datetime(row["acknowledged_at"]),
            error=row["error"],
            attempt_count=row["attempt_count"],
            last_attempt_at=_datetime(row["last_attempt_at"]),
            next_retry_at=_datetime(row["next_retry_at"]),
            expires_at=_datetime(row["expires_at"]),
        )


class SqlitePolicyProfileRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def add(self, profile: PolicyProfileDefinition) -> None:
        self._connection.execute(
            """
            INSERT INTO policy_profiles(id, label, description, rules, is_builtin)
            VALUES (?, ?, ?, ?, ?)
            """,
            self._values(profile),
        )

    def get(self, profile_id: str) -> PolicyProfileDefinition:
        profile = self.find(profile_id)
        if profile is None:
            raise EntityNotFoundError(f"policy profile not found: {profile_id}")
        return profile

    def find(self, profile_id: str) -> PolicyProfileDefinition | None:
        row = self._connection.execute(
            "SELECT * FROM policy_profiles WHERE id = ?",
            (profile_id.strip().upper(),),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def save(self, profile: PolicyProfileDefinition) -> None:
        cursor = self._connection.execute(
            """
            UPDATE policy_profiles
               SET label = ?, description = ?, rules = ?, is_builtin = ?
             WHERE id = ?
            """,
            (
                profile.label,
                profile.description,
                canonical_json(profile.rules),
                int(profile.is_builtin),
                profile.id,
            ),
        )
        if cursor.rowcount != 1:
            raise EntityNotFoundError(f"policy profile not found: {profile.id}")

    def delete(self, profile_id: str) -> None:
        cursor = self._connection.execute(
            "DELETE FROM policy_profiles WHERE id = ?",
            (profile_id.strip().upper(),),
        )
        if cursor.rowcount != 1:
            raise EntityNotFoundError(f"policy profile not found: {profile_id}")

    def list_all(self) -> list[PolicyProfileDefinition]:
        rows = self._connection.execute(
            "SELECT * FROM policy_profiles ORDER BY is_builtin DESC, id"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _values(profile: PolicyProfileDefinition) -> tuple:
        return (
            profile.id,
            profile.label,
            profile.description,
            canonical_json(profile.rules),
            int(profile.is_builtin),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PolicyProfileDefinition:
        return PolicyProfileDefinition(
            id=row["id"],
            label=row["label"],
            description=row["description"],
            rules=json.loads(row["rules"]),
            is_builtin=bool(row["is_builtin"]),
        )


class SqliteTelemetryRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def add(self, event: TelemetryEvent) -> None:
        self._connection.execute(
            """
            INSERT INTO telemetry_events(
                id, session_id, workstation_id, event_type, severity, category,
                action, destination, correlation_id, payload, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.session_id,
                event.workstation_id,
                event.event_type,
                event.severity.value,
                event.category,
                event.action,
                event.destination,
                event.correlation_id,
                canonical_json(event.payload),
                event.occurred_at.isoformat(),
            ),
        )

    def list_for_session(self, session_id: str) -> list[TelemetryEvent]:
        rows = self._connection.execute(
            "SELECT * FROM telemetry_events WHERE session_id = ? ORDER BY occurred_at, id",
            (session_id,),
        ).fetchall()
        return [
            TelemetryEvent(
                id=row["id"],
                session_id=row["session_id"],
                workstation_id=row["workstation_id"],
                event_type=row["event_type"],
                severity=Severity(row["severity"]),
                category=row["category"],
                action=row["action"],
                destination=row["destination"],
                correlation_id=row["correlation_id"],
                payload=json.loads(row["payload"]),
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
            )
            for row in rows
        ]


class SqliteIncidentRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def add(self, incident: Incident) -> None:
        self._connection.execute(
            """
            INSERT INTO incidents(
                id, session_id, workstation_id, category, severity, status,
                evidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident.id,
                incident.session_id,
                incident.workstation_id,
                incident.category,
                incident.severity.value,
                incident.status.value,
                canonical_json(incident.evidence),
                incident.created_at.isoformat(),
            ),
        )

    def list_for_session(self, session_id: str) -> list[Incident]:
        rows = self._connection.execute(
            "SELECT * FROM incidents WHERE session_id = ? ORDER BY created_at, id",
            (session_id,),
        ).fetchall()
        return [
            Incident(
                id=row["id"],
                session_id=row["session_id"],
                workstation_id=row["workstation_id"],
                category=row["category"],
                severity=Severity(row["severity"]),
                status=IncidentStatus(row["status"]),
                evidence=json.loads(row["evidence"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]


class SqliteAuditRepository:
    GENESIS_HASH = "0" * 64

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def append(
        self, session_id: str, actor: str, action: str, target: str, details: dict
    ) -> AuditEvent:
        previous = self._connection.execute(
            """
            SELECT chain_hash FROM audit_events
             WHERE session_id = ? ORDER BY sequence DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        previous_hash = previous["chain_hash"] if previous else self.GENESIS_HASH
        occurred_at = utc_now()
        payload = {
            "session_id": session_id,
            "actor": actor,
            "action": action,
            "target": target,
            "details": details,
            "occurred_at": occurred_at.isoformat(),
        }
        event = AuditEvent(
            session_id=session_id,
            actor=actor,
            action=action,
            target=target,
            details=details,
            previous_hash=previous_hash,
            chain_hash=compute_audit_hash(previous_hash, payload),
            occurred_at=occurred_at,
        )
        self._connection.execute(
            """
            INSERT INTO audit_events(
                id, session_id, actor, action, target, details,
                previous_hash, chain_hash, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.session_id,
                event.actor,
                event.action,
                event.target,
                canonical_json(event.details),
                event.previous_hash,
                event.chain_hash,
                event.occurred_at.isoformat(),
            ),
        )
        return event

    def list_for_session(self, session_id: str) -> list[AuditEvent]:
        rows = self._connection.execute(
            "SELECT * FROM audit_events WHERE session_id = ? ORDER BY sequence",
            (session_id,),
        ).fetchall()
        return [
            AuditEvent(
                id=row["id"],
                session_id=row["session_id"],
                actor=row["actor"],
                action=row["action"],
                target=row["target"],
                details=json.loads(row["details"]),
                previous_hash=row["previous_hash"],
                chain_hash=row["chain_hash"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
            )
            for row in rows
        ]

    def verify_chain(self, session_id: str) -> bool:
        previous_hash = self.GENESIS_HASH
        for event in self.list_for_session(session_id):
            if event.previous_hash != previous_hash:
                return False
            payload = {
                "session_id": event.session_id,
                "actor": event.actor,
                "action": event.action,
                "target": event.target,
                "details": event.details,
                "occurred_at": event.occurred_at.isoformat(),
            }
            if compute_audit_hash(previous_hash, payload) != event.chain_hash:
                return False
            previous_hash = event.chain_hash
        return True


class SqliteUnitOfWork:
    def __init__(self, database: SqliteDatabase):
        self._database = database
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> SqliteUnitOfWork:
        self._connection = self._database.connect()
        self._connection.execute("BEGIN IMMEDIATE")
        self.agents = SqliteAgentRepository(self._connection)
        self.sessions = SqliteSessionRepository(self._connection)
        self.session_workstations = SqliteSessionWorkstationRepository(self._connection)
        self.commands = SqliteCommandRepository(self._connection)
        self.policy_profiles = SqlitePolicyProfileRepository(self._connection)
        self.telemetry = SqliteTelemetryRepository(self._connection)
        self.incidents = SqliteIncidentRepository(self._connection)
        self.audits = SqliteAuditRepository(self._connection)
        return self

    def commit(self) -> None:
        self._require_connection().commit()

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        connection = self._require_connection()
        if exc_type is not None:
            connection.rollback()
        connection.close()
        self._connection = None
        return False

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("unit of work is not active")
        return self._connection


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
