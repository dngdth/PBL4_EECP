from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.services.policy_profiles import BUILT_IN_POLICY_PROFILES
from app.domain.value_objects.primitives import canonical_json


class _CompatConnection:
    """Small DB-API compatibility layer for the existing repository adapters."""

    def __init__(self, connection: Any, pool: Any):
        self._connection = connection
        self._pool = pool

    def execute(self, query: str, params: tuple = ()):
        return self._connection.execute(self._postgres_query(query), params)

    def executemany(self, query: str, params_seq):
        with self._connection.cursor() as cursor:
            cursor.executemany(self._postgres_query(query), params_seq)

    @staticmethod
    def _postgres_query(query: str) -> str:
        return query.replace("?", "%s").replace(
            "ORDER BY rowid DESC",
            "ORDER BY ((payload::jsonb)->>'created_at')::timestamptz DESC, id DESC",
        )

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        # Read-only repository calls still open a PostgreSQL transaction. End it
        # explicitly before returning the connection to avoid pool-side rollback.
        self._connection.rollback()
        self._pool.putconn(self._connection)


class PostgresDatabase:
    """Production business store; domain/application remain driver-independent."""

    def __init__(self, database_url: str, *, min_pool_size: int = 1, max_pool_size: int = 10):
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL URL")
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("PostgreSQL support requires psycopg[pool]") from exc
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self._dict_row = dict_row
        self._pool = ConnectionPool(
            self.database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    def connect(self) -> _CompatConnection:
        return _CompatConnection(self._pool.getconn(), self._pool)

    def require_schema(self) -> None:
        try:
            with self._pool.connection() as connection:
                row = connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
                ).fetchone()
        except Exception as exc:
            raise RuntimeError(
                "PostgreSQL is unavailable or not migrated; run `python -m "
                "app.infrastructure.persistence.migrate`"
            ) from exc
        if row is None:
            raise RuntimeError("PostgreSQL schema has no applied migration")

    def migrate(self) -> None:
        migration = Path(__file__).with_name("migrations") / "0001_initial.sql"
        with self._pool.connection() as connection:
            connection.execute(migration.read_text(encoding="utf-8"))
            for profile in BUILT_IN_POLICY_PROFILES.list_all():
                connection.execute(
                    """
                    INSERT INTO policy_profiles(id, label, description, rules, is_builtin)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        profile.id,
                        profile.label,
                        profile.description,
                        canonical_json(profile.rules),
                        int(profile.is_builtin),
                    ),
                )
            connection.commit()

    def health(self) -> bool:
        try:
            with self._pool.connection() as connection:
                return connection.execute("SELECT 1").fetchone() is not None
        except Exception:
            return False

    def close(self) -> None:
        self._pool.close()

    def unit_of_work(self):
        return PostgresUnitOfWork(self)


class PostgresUnitOfWork:
    def __init__(self, database: PostgresDatabase):
        self._database = database
        self._connection: _CompatConnection | None = None

    def __enter__(self):
        from app.infrastructure.repositories.sqlite import (
            SqliteAgentGatewayBindingRepository,
            SqliteAgentRepository,
            SqliteAuditRepository,
            SqliteCommandRepository,
            SqliteGatewayRepository,
            SqliteIncidentRepository,
            SqlitePolicyProfileRepository,
            SqliteSessionRepository,
            SqliteSessionWorkstationRepository,
            SqliteTelemetryRepository,
        )

        self._connection = self._database.connect()
        connection = self._connection
        self.agents = SqliteAgentRepository(connection)
        self.gateways = SqliteGatewayRepository(connection)
        self.agent_gateway_bindings = SqliteAgentGatewayBindingRepository(connection)
        self.sessions = SqliteSessionRepository(connection)
        self.session_workstations = SqliteSessionWorkstationRepository(connection)
        self.commands = SqliteCommandRepository(connection)
        self.policy_profiles = SqlitePolicyProfileRepository(connection)
        self.telemetry = SqliteTelemetryRepository(connection)
        self.incidents = SqliteIncidentRepository(connection)
        self.audits = SqliteAuditRepository(connection)
        return self

    def commit(self) -> None:
        if self._connection is None:
            raise RuntimeError("unit of work is not active")
        self._connection.commit()

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if self._connection is None:
            raise RuntimeError("unit of work is not active")
        if exc_type is not None:
            self._connection.rollback()
        self._connection.close()
        self._connection = None
        return False
