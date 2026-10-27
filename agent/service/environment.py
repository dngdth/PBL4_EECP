from __future__ import annotations

import os
from pathlib import Path

SERVICE_ENVIRONMENT_NAMES = frozenset(
    {
        "EECP_AGENT_ID",
        "EECP_AGENT_VERSION",
        "EECP_COMMAND_REPLAY_PATH",
        "EECP_COMMAND_VERIFICATION_KEY",
        "EECP_ENVIRONMENT",
        "EECP_GATEWAY_URL",
        "EECP_PIPE_NAME",
        "EECP_POLICY_MODE",
        "EECP_POLICY_STATE_PATH",
        "EECP_POLICY_VERIFICATION_KEY",
        "EECP_REQUIRE_AUTHORIZED_COMMANDS",
        "EECP_REQUIRE_SIGNED_POLICY",
        "EECP_SECURITY_EVENT_JOURNAL_MAX_ROWS",
        "EECP_SECURITY_EVENT_JOURNAL_PATH",
        "EECP_SECURITY_EVENT_RETENTION_DAYS",
        "EECP_SERVICE_DATA_ROOT",
    }
)


def default_service_environment_path() -> Path:
    return Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "EECP" / "service.env"


def load_service_environment(path: Path | None = None) -> None:
    """Load the protected LocalSystem-only Service configuration before config import."""
    source = path or default_service_environment_path()
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OSError(f"cannot read protected Service environment: {source}") from exc

    parsed: dict[str, str] = {}
    for line_number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or name not in SERVICE_ENVIRONMENT_NAMES:
            raise ValueError(
                f"invalid Service environment entry at line {line_number}: {name or '<empty>'}"
            )
        if name in parsed:
            raise ValueError(f"duplicate Service environment entry: {name}")
        parsed[name] = value.strip()
    for name, value in parsed.items():
        os.environ[name] = value
