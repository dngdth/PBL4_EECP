from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    gateway_id: str
    room_id: str
    version: str
    listen_host: str
    listen_port: int
    tls_certfile: str | None
    tls_keyfile: str | None
    backend_ws_url: str
    gateway_bootstrap_token: str
    agent_bootstrap_token: str
    allow_plaintext_ws: bool
    reconnect_initial_seconds: float
    reconnect_max_seconds: float
    presence_timeout_seconds: float
    health_interval_seconds: float
    presence_offline_timeout_seconds: float = 45.0
    event_buffer_path: str = ""
    event_buffer_max_rows: int = 10_000
    event_buffer_degraded_threshold: int = 8_000
    event_retry_initial_seconds: float = 1.0
    event_retry_max_seconds: float = 30.0
    event_retry_jitter_ratio: float = 0.25
    event_flush_interval_seconds: float = 0.5
    event_flush_batch_size: int = 50
    agent_credentials_json: str = ""
    environment: str = "development"
    backend_queue_max_messages: int = 4096

    @classmethod
    def from_env(cls) -> GatewaySettings:
        allow_plaintext = _bool_env("EECP_GATEWAY_ALLOW_PLAINTEXT_WS", False)
        max_rows = int(os.getenv("EECP_GATEWAY_EVENT_BUFFER_MAX_ROWS", "10000"))
        default_threshold = str(max(1, int(max_rows * 0.8)))
        settings = cls(
            gateway_id=_required_env("EECP_GATEWAY_ID"),
            room_id=_required_env("EECP_GATEWAY_ROOM_ID"),
            version=os.getenv("EECP_GATEWAY_VERSION", "1.0.0").strip(),
            listen_host=os.getenv("EECP_GATEWAY_LISTEN_HOST", "0.0.0.0").strip(),
            listen_port=int(os.getenv("EECP_GATEWAY_LISTEN_PORT", "8443")),
            tls_certfile=os.getenv("EECP_GATEWAY_TLS_CERTFILE") or None,
            tls_keyfile=os.getenv("EECP_GATEWAY_TLS_KEYFILE") or None,
            backend_ws_url=os.getenv(
                "EECP_GATEWAY_BACKEND_WS_URL",
                "wss://127.0.0.1:8000/api/v2/gateways/ws",
            ).rstrip("/"),
            gateway_bootstrap_token=_required_env("EECP_GATEWAY_BOOTSTRAP_TOKEN"),
            agent_bootstrap_token=_required_env("EECP_AGENT_GATEWAY_TOKEN"),
            allow_plaintext_ws=allow_plaintext,
            reconnect_initial_seconds=float(
                os.getenv("EECP_GATEWAY_RECONNECT_INITIAL_SECONDS", "1")
            ),
            reconnect_max_seconds=float(os.getenv("EECP_GATEWAY_RECONNECT_MAX_SECONDS", "30")),
            presence_timeout_seconds=float(
                os.getenv("EECP_GATEWAY_PRESENCE_TIMEOUT_SECONDS", "15")
            ),
            health_interval_seconds=float(os.getenv("EECP_GATEWAY_HEALTH_INTERVAL_SECONDS", "5")),
            presence_offline_timeout_seconds=float(
                os.getenv("EECP_GATEWAY_PRESENCE_OFFLINE_TIMEOUT_SECONDS", "45")
            ),
            event_buffer_path=os.getenv(
                "EECP_GATEWAY_EVENT_BUFFER_PATH",
                str(Path(os.getenv("LOCALAPPDATA", "data")) / "EECP" / "gateway-events.db"),
            ),
            event_buffer_max_rows=max_rows,
            event_buffer_degraded_threshold=int(
                os.getenv("EECP_GATEWAY_EVENT_BUFFER_DEGRADED_THRESHOLD", default_threshold)
            ),
            event_retry_initial_seconds=float(
                os.getenv("EECP_GATEWAY_EVENT_RETRY_INITIAL_SECONDS", "1")
            ),
            event_retry_max_seconds=float(os.getenv("EECP_GATEWAY_EVENT_RETRY_MAX_SECONDS", "30")),
            event_retry_jitter_ratio=float(
                os.getenv("EECP_GATEWAY_EVENT_RETRY_JITTER_RATIO", "0.25")
            ),
            event_flush_interval_seconds=float(
                os.getenv("EECP_GATEWAY_EVENT_FLUSH_INTERVAL_SECONDS", "0.5")
            ),
            event_flush_batch_size=int(os.getenv("EECP_GATEWAY_EVENT_FLUSH_BATCH_SIZE", "50")),
            agent_credentials_json=os.getenv("EECP_AGENT_CREDENTIALS_JSON", "").strip(),
            environment=os.getenv("EECP_ENVIRONMENT", "development").strip().lower(),
            backend_queue_max_messages=int(
                os.getenv("EECP_GATEWAY_BACKEND_QUEUE_MAX_MESSAGES", "4096")
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        scheme = urlparse(self.backend_ws_url).scheme
        if scheme not in {"ws", "wss"}:
            raise ValueError("backend WebSocket URL must use ws:// or wss://")
        if scheme == "ws" and not self.allow_plaintext_ws:
            raise ValueError("plaintext ws:// requires EECP_GATEWAY_ALLOW_PLAINTEXT_WS=true")
        if self.environment == "production-like":
            if scheme != "wss" or self.allow_plaintext_ws:
                raise ValueError("production-like Gateway requires WSS without plaintext")
            if not _has_machine_credentials(self.agent_credentials_json):
                raise ValueError("EECP_AGENT_CREDENTIALS_JSON must contain per-Agent credentials")
            for name, value in (
                ("EECP_GATEWAY_BOOTSTRAP_TOKEN", self.gateway_bootstrap_token),
                ("EECP_AGENT_GATEWAY_TOKEN", self.agent_bootstrap_token),
            ):
                if _is_insecure_development_secret(value):
                    raise ValueError(f"{name} contains an insecure development value")
        if not 1 <= self.listen_port <= 65535:
            raise ValueError("gateway listen port is invalid")
        if bool(self.tls_certfile) != bool(self.tls_keyfile):
            raise ValueError("both Gateway TLS certificate and key are required")
        if not self.allow_plaintext_ws and not self.tls_certfile:
            raise ValueError("Gateway Agent WSS requires TLS files; plaintext listener is dev-only")
        if self.reconnect_initial_seconds <= 0:
            raise ValueError("initial reconnect delay must be positive")
        if self.reconnect_max_seconds < self.reconnect_initial_seconds:
            raise ValueError("maximum reconnect delay must not be smaller than initial")
        if self.presence_timeout_seconds <= 0 or self.health_interval_seconds <= 0:
            raise ValueError("presence and health intervals must be positive")
        if self.presence_offline_timeout_seconds <= self.presence_timeout_seconds:
            raise ValueError("presence offline timeout must exceed stale timeout")
        if self.event_buffer_max_rows <= 0 or not (
            0 < self.event_buffer_degraded_threshold <= self.event_buffer_max_rows
        ):
            raise ValueError("Gateway event buffer limits are invalid")
        if self.event_retry_initial_seconds <= 0 or (
            self.event_retry_max_seconds < self.event_retry_initial_seconds
        ):
            raise ValueError("Gateway event retry delays are invalid")
        if not 0 <= self.event_retry_jitter_ratio <= 1:
            raise ValueError("Gateway event retry jitter ratio is invalid")
        if self.event_flush_interval_seconds <= 0 or self.event_flush_batch_size <= 0:
            raise ValueError("Gateway event flush settings must be positive")
        if self.backend_queue_max_messages <= 0:
            raise ValueError("Gateway Backend queue limit must be positive")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_insecure_development_secret(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {
        "admin",
        "admin/admin",
        "password",
        "changeme",
        "test-secret",
        "test-token",
    } or any(marker in normalized for marker in ("replace-with", "change-me"))


def _has_machine_credentials(value: str) -> bool:
    try:
        records = json.loads(value)
    except json.JSONDecodeError:
        return False
    if not isinstance(records, dict) or not records:
        return False
    return all(
        isinstance(record, dict)
        and isinstance(record.get("secret_sha256"), str)
        and len(record["secret_sha256"]) == 64
        and all(character in "0123456789abcdefABCDEF" for character in record["secret_sha256"])
        for record in records.values()
    )
