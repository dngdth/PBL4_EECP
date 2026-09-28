from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta

from app.application.security import Principal, Role


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    actual_salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), actual_salt, 210_000)
    return f"pbkdf2-sha256${actual_salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_hex, expected = encoded.split("$", 2)
        if algorithm != "pbkdf2-sha256":
            return False
        actual = hash_password(password, salt=bytes.fromhex(salt_hex)).split("$", 2)[2]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class TokenService:
    def __init__(self, key: str, lifetime_seconds: int = 3600):
        if len(key) < 32:
            raise ValueError("authentication signing key must contain at least 32 characters")
        self._key = key.encode()
        self._lifetime = lifetime_seconds

    def issue(self, principal: Principal, now: datetime | None = None) -> str:
        issued = now or datetime.now(UTC)
        payload = {
            "sub": principal.subject,
            "role": principal.role.value,
            "sessions": sorted(principal.session_scope),
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(seconds=self._lifetime)).timestamp()),
        }
        encoded = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = _b64(hmac.new(self._key, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify(self, token: str, now: datetime | None = None) -> Principal:
        try:
            encoded, supplied = token.split(".", 1)
            expected = _b64(hmac.new(self._key, encoded.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied, expected):
                raise ValueError("invalid token signature")
            payload = json.loads(_unb64(encoded))
            current = int((now or datetime.now(UTC)).timestamp())
            if current >= int(payload["exp"]):
                raise ValueError("token expired")
            return Principal(
                str(payload["sub"]),
                Role(payload["role"]),
                frozenset(str(item) for item in payload.get("sessions", [])),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid or expired authentication token") from exc


class MachineCredentialRegistry:
    """Identity-bound hashed credentials loaded from protected deployment config."""

    def __init__(self, encoded: str):
        try:
            values = json.loads(encoded) if encoded else {}
        except json.JSONDecodeError as exc:
            raise ValueError("machine credential configuration is invalid") from exc
        if not isinstance(values, dict):
            raise ValueError("machine credential configuration must be an object")
        self._values = values

    def authenticate(self, identity: str, secret: str) -> bool:
        return self.failure_reason(identity, secret) is None

    def failure_reason(self, identity: str, secret: str) -> str | None:
        record = self._values.get(identity)
        if not isinstance(record, dict):
            return "UNKNOWN_IDENTITY"
        if record.get("revoked") is True:
            return "REVOKED_CREDENTIAL"
        expected = record.get("secret_sha256")
        if not isinstance(expected, str):
            return "CREDENTIAL_NOT_CONFIGURED"
        actual = hashlib.sha256(secret.encode()).hexdigest()
        return None if hmac.compare_digest(actual, expected) else "INVALID_CREDENTIAL"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
