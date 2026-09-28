from __future__ import annotations

import os
from dataclasses import dataclass
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

    @classmethod
    def from_env(cls) -> GatewaySettings:
        allow_plaintext = _bool_env("EECP_GATEWAY_ALLOW_PLAINTEXT_WS", False)
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
            reconnect_max_seconds=float(
                os.getenv("EECP_GATEWAY_RECONNECT_MAX_SECONDS", "30")
            ),
            presence_timeout_seconds=float(
                os.getenv("EECP_GATEWAY_PRESENCE_TIMEOUT_SECONDS", "15")
            ),
            health_interval_seconds=float(
                os.getenv("EECP_GATEWAY_HEALTH_INTERVAL_SECONDS", "5")
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
        if not 1 <= self.listen_port <= 65535:
            raise ValueError("gateway listen port is invalid")
        if bool(self.tls_certfile) != bool(self.tls_keyfile):
            raise ValueError("both Gateway TLS certificate and key are required")
        if not self.allow_plaintext_ws and not self.tls_certfile:
            raise ValueError(
                "Gateway Agent WSS requires TLS files; plaintext listener is dev-only"
            )
        if self.reconnect_initial_seconds <= 0:
            raise ValueError("initial reconnect delay must be positive")
        if self.reconnect_max_seconds < self.reconnect_initial_seconds:
            raise ValueError("maximum reconnect delay must not be smaller than initial")
        if self.presence_timeout_seconds <= 0 or self.health_interval_seconds <= 0:
            raise ValueError("presence and health intervals must be positive")


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
