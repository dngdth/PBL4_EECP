from __future__ import annotations

import os

from app.infrastructure.persistence.postgres import PostgresDatabase


def main() -> None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    PostgresDatabase(database_url).migrate()
    print("database migration complete")


if __name__ == "__main__":
    main()
