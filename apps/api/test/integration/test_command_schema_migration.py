import sqlite3
from pathlib import Path

from app.infrastructure.persistence.database import SqliteDatabase


def test_initialize_adds_command_delivery_columns_to_existing_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE commands (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            acknowledged_at TEXT,
            error TEXT
        )
        """
    )
    connection.commit()
    connection.close()

    database = SqliteDatabase(path)
    database.initialize()

    with database.connect() as migrated:
        columns = {
            row["name"] for row in migrated.execute("PRAGMA table_info(commands)")
        }
        indexes = {
            row["name"] for row in migrated.execute("PRAGMA index_list(commands)")
        }

    assert {
        "attempt_count",
        "last_attempt_at",
        "next_retry_at",
        "expires_at",
    }.issubset(columns)
    assert "ix_commands_delivery" in indexes

    with database.connect() as migrated:
        profiles = migrated.execute(
            "SELECT id, is_builtin FROM policy_profiles ORDER BY id"
        ).fetchall()

    assert {row["id"] for row in profiles} == {
        "INTERNET_NO_AI",
        "OFFLINE_PROGRAMMING",
    }
    assert all(row["is_builtin"] == 1 for row in profiles)
