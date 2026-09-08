import os
from pathlib import Path


def load_agent_id() -> str:
    agent_id = os.getenv("EECP_AGENT_ID", "").strip()
    if not agent_id:
        raise RuntimeError(
            "EECP_AGENT_ID is required. Example: $env:EECP_AGENT_ID='PC02'"
        )
    return agent_id


SERVER_URL = os.getenv("EECP_SERVER_URL", "http://192.168.3.50:8000").rstrip("/")
AGENT_VERSION = os.getenv("EECP_AGENT_VERSION", "1.1.0")
HEARTBEAT_INTERVAL_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 5
POLICY_MODE = os.getenv("EECP_POLICY_MODE", "enforce").strip().lower()
POLICY_STATE_PATH = Path(
    os.getenv(
        "EECP_POLICY_STATE_PATH",
        str(Path(os.getenv("LOCALAPPDATA", "data")) / "EECP" / "policy-state.json"),
    )
)
