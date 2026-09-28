from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    gateway_bootstrap_token: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("EECP_GATEWAY_BOOTSTRAP_TOKEN", "").strip()
        return cls(
            database_path=Path(
                os.getenv("EECP_DATABASE_PATH", "./data/eecp.db")
            ).resolve(),
            gateway_bootstrap_token=token or None,
        )

