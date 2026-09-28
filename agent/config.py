import os
from pathlib import Path


def load_agent_id() -> str:
    agent_id = os.getenv("EECP_AGENT_ID", "").strip()
    if not agent_id:
        raise RuntimeError(
            "EECP_AGENT_ID is required. Example: $env:EECP_AGENT_ID='PC02'"
        )
    return agent_id


SERVER_URL = os.getenv("EECP_SERVER_URL", "http://172.20.10.3:8000").rstrip("/")
GATEWAY_URL = os.getenv(
    "EECP_GATEWAY_URL", "wss://127.0.0.1:8443/ws/agents"
).strip()
GATEWAY_ALLOW_PLAINTEXT_WS = os.getenv(
    "EECP_GATEWAY_ALLOW_PLAINTEXT_WS", "false"
).strip().lower() in {"1", "true", "yes", "on"}
GATEWAY_RECONNECT_INITIAL_SECONDS = 1.0
GATEWAY_RECONNECT_MAX_SECONDS = 30.0
AGENT_VERSION = os.getenv("EECP_AGENT_VERSION", "1.1.0")
HEARTBEAT_INTERVAL_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 5
IPC_PIPE_NAME = os.getenv("EECP_PIPE_NAME", r"\\.\pipe\eecp-agent-v2")
IPC_CONNECT_TIMEOUT_SECONDS = 2.0
IPC_REQUEST_TIMEOUT_SECONDS = 10.0
IPC_MAX_MESSAGE_BYTES = 1024 * 1024
IPC_REPLAY_CACHE_SIZE = 1024
SERVICE_MAINTENANCE_INTERVAL_SECONDS = 5.0
POLICY_MODE = os.getenv("EECP_POLICY_MODE", "enforce").strip().lower()
POLICY_STATE_PATH = Path(
    os.getenv(
        "EECP_POLICY_STATE_PATH",
        str(Path(os.getenv("LOCALAPPDATA", "data")) / "EECP" / "policy-state.json"),
    )
)


def load_agent_gateway_token() -> str:
    token = os.getenv("EECP_AGENT_GATEWAY_TOKEN", "").strip()
    if not token:
        raise RuntimeError("EECP_AGENT_GATEWAY_TOKEN is required for Gateway transport")
    return token
