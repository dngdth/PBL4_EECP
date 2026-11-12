"""Service-owned last-known-good state and persistent per-session policy watermarks."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from pathlib import Path

from agent.domain.policy import parse_policy_payload


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class ProtectedPolicyEnforcer:
    """Wrap OS enforcement; the sealed journal must have SYSTEM/Admin-only ACLs.

    The journal survives restore and Service restart. Ordinary policy-state files
    cannot replace its last-known-good snapshot. It is not a TPM monotonic counter:
    a privileged attacker restoring the entire protected directory is out of scope.
    """

    def __init__(self, enforcer, state_path: Path, journal_path: Path, key: str):
        if not key:
            raise ValueError("protected policy journal requires a verification key")
        self.inner = enforcer
        self.state_path = state_path
        self.journal_path = journal_path
        self.key = key.encode()
        self.lock = threading.RLock()
        self._sink = None
        self._pending_tamper = False
        self._error: str | None = None
        try:
            sealed = json.loads(journal_path.read_text("utf-8"))
        except FileNotFoundError:
            if state_path.exists():
                raise OSError(
                    "active policy state has no protected journal; authorized migration required"
                ) from None
            self.value = {"version": 1, "watermarks": {}, "state": None, "payload": None}
            self._save()
        else:
            value = sealed.get("value")
            expected = hmac.new(self.key, _canonical(value), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(str(sealed.get("mac", "")), expected):
                raise OSError("protected policy journal authentication failed")
            if value.get("version") != 1 or not isinstance(value.get("watermarks"), dict):
                raise OSError("protected policy journal format is invalid")
            self.value = value
        self._recover_state()

    @property
    def health_error(self):
        return self._error or getattr(self.inner, "health_error", None)

    def set_event_sink(self, sink) -> None:
        self._sink = sink
        inner_setter = getattr(self.inner, "set_event_sink", None)
        if inner_setter:
            inner_setter(sink)
        if self._pending_tamper:
            self._record("STATE_RECOVERED")
            self._pending_tamper = False

    def _record(self, action: str) -> None:
        state = self.value.get("state") or {}
        if self._sink and state.get("session_id"):
            self._sink(
                session_id=state["session_id"],
                event_type="POLICY_INTEGRITY",
                category="POLICY_STATE_INTEGRITY",
                action=action,
                severity="WARNING",
                source="protected-policy-journal",
                details={"target": "policy-state"},
            )

    def _save(self) -> None:
        mac = hmac.new(self.key, _canonical(self.value), hashlib.sha256).hexdigest()
        _atomic(self.journal_path, _canonical({"value": self.value, "mac": mac}))

    def _recover_state(self) -> None:
        try:
            actual = json.loads(self.state_path.read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            actual = None
        expected = self.value["state"]
        if actual == expected and (expected is not None or not self.state_path.exists()):
            return
        self._record("STATE_TAMPER_DETECTED")
        if expected is None:
            self.state_path.unlink(missing_ok=True)
        else:
            _atomic(self.state_path, _canonical(expected))
        self._record("STATE_RECOVERED")
        if self._sink is None:
            self._pending_tamper = True

    def apply(self, payload: dict) -> str:
        with self.lock:
            self._recover_state()
            policy = parse_policy_payload(payload)
            previous = self.value["watermarks"].get(policy.session_id)
            if previous and (
                policy.version < previous["version"]
                or (
                    policy.version == previous["version"] and policy.policy_hash != previous["hash"]
                )
            ):
                self._record("POLICY_ROLLBACK_REJECTED")
                raise ValueError("policy version rollback or same-version content change rejected")
            old = json.loads(json.dumps(self.value))
            try:
                result = self.inner.apply(payload)
                state = json.loads(self.state_path.read_text("utf-8"))
                if result != policy.policy_hash or state.get("policy_hash") != policy.policy_hash:
                    raise OSError("policy apply verification failed")
                if getattr(self.inner, "health_error", None):
                    raise OSError(str(self.inner.health_error))
                self.value["state"] = state
                self.value["payload"] = payload
                self.value["watermarks"][policy.session_id] = {
                    "version": policy.version,
                    "hash": policy.policy_hash,
                }
                self._save()
                self._error = None
                return result
            except (OSError, ValueError):
                self.value = old
                try:
                    if old["payload"]:
                        self.inner.apply(old["payload"])
                    else:
                        self.inner.restore()
                    if getattr(self.inner, "health_error", None):
                        raise OSError(str(self.inner.health_error))
                    self._recover_state()
                    self._record("SAFE_APPLY_ROLLED_BACK")
                except (OSError, ValueError) as recovery_error:
                    self._error = f"safe apply recovery failed: {recovery_error}"
                    self._record("SAFE_APPLY_RECOVERY_FAILED")
                raise

    def maintain(self) -> None:
        with self.lock:
            self._recover_state()
            self.inner.maintain()
            if self.state_path.exists():
                self.value["state"] = json.loads(self.state_path.read_text("utf-8"))
            self._save()
            self._error = None

    def restore(self) -> None:
        with self.lock:
            self._recover_state()
            self.inner.restore()
            self.value["state"] = None
            self.value["payload"] = None
            self._save()  # Watermarks remain: baseline restore cannot enable a downgrade.
