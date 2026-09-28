from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    gateway_bootstrap_token: str | None = None
    database_url: str | None = None
    redis_url: str | None = None
    environment: str = "development"
    presence_ttl_seconds: int = 30
    auth_signing_key: str | None = None
    examiner_username: str | None = None
    examiner_password_hash: str | None = None
    gateway_credentials_json: str | None = None
    examiner_role: str = "EXAMINER"

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("EECP_GATEWAY_BOOTSTRAP_TOKEN", "").strip()
        environment = os.getenv("EECP_ENVIRONMENT", "development").strip().lower()
        settings = cls(
            database_path=Path(os.getenv("EECP_DATABASE_PATH", "./data/eecp.db")).resolve(),
            gateway_bootstrap_token=token or None,
            database_url=os.getenv("DATABASE_URL", "").strip() or None,
            redis_url=os.getenv("REDIS_URL", "").strip() or None,
            environment=environment,
            presence_ttl_seconds=int(os.getenv("EECP_PRESENCE_TTL_SECONDS", "30")),
            auth_signing_key=os.getenv("EECP_AUTH_SIGNING_KEY", "").strip() or None,
            examiner_username=os.getenv("EECP_EXAMINER_USERNAME", "").strip() or None,
            examiner_password_hash=(os.getenv("EECP_EXAMINER_PASSWORD_HASH", "").strip() or None),
            gateway_credentials_json=(
                os.getenv("EECP_GATEWAY_CREDENTIALS_JSON", "").strip() or None
            ),
            examiner_role=os.getenv("EECP_EXAMINER_ROLE", "EXAMINER").strip().upper(),
        )
        if environment == "production-like":
            if not settings.database_url:
                raise ValueError("DATABASE_URL is required in production-like mode")
            if not settings.redis_url:
                raise ValueError("REDIS_URL is required in production-like mode")
            if not settings.gateway_bootstrap_token:
                raise ValueError("Gateway credentials are required in production-like mode")
            if not all(
                (
                    settings.auth_signing_key,
                    settings.examiner_username,
                    settings.examiner_password_hash,
                    settings.gateway_credentials_json,
                    os.getenv("EECP_POLICY_SIGNING_KEY", "").strip(),
                    os.getenv("EECP_COMMAND_SIGNING_KEY", "").strip(),
                )
            ):
                raise ValueError(
                    "Examiner authentication and policy signing are required in "
                    "production-like mode"
                )
            if not _has_machine_credentials(settings.gateway_credentials_json):
                raise ValueError(
                    "EECP_GATEWAY_CREDENTIALS_JSON must contain per-Gateway credentials"
                )
            secrets = {
                "EECP_GATEWAY_BOOTSTRAP_TOKEN": settings.gateway_bootstrap_token,
                "EECP_AUTH_SIGNING_KEY": settings.auth_signing_key,
                "EECP_EXAMINER_PASSWORD_HASH": settings.examiner_password_hash,
                "EECP_POLICY_SIGNING_KEY": os.getenv("EECP_POLICY_SIGNING_KEY", "").strip(),
                "EECP_COMMAND_SIGNING_KEY": os.getenv(
                    "EECP_COMMAND_SIGNING_KEY", ""
                ).strip(),
            }
            for name, value in secrets.items():
                if _is_insecure_development_secret(value):
                    raise ValueError(f"{name} contains an insecure development value")
        if settings.presence_ttl_seconds <= 0:
            raise ValueError("presence TTL must be positive")
        if settings.examiner_role not in {"ADMIN", "EXAMINER"}:
            raise ValueError("EECP_EXAMINER_ROLE must be ADMIN or EXAMINER")
        return settings


def _is_insecure_development_secret(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return normalized in {
        "admin",
        "admin/admin",
        "password",
        "changeme",
        "test-secret",
        "test-token",
    } or any(marker in normalized for marker in ("replace-with", "change-me"))


def _has_machine_credentials(value: str | None) -> bool:
    try:
        records = json.loads(value or "")
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
