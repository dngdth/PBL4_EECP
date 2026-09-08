from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from agent.domain.policy import PolicySpecification, parse_policy_payload

POLICY_MARKER_START = "# BEGIN EECP MANAGED POLICY"
POLICY_MARKER_END = "# END EECP MANAGED POLICY"
USBSTOR_KEY = r"HKLM\SYSTEM\CurrentControlSet\Services\USBSTOR"

CATEGORY_DOMAINS = {
    "generative_ai": {
        "chatgpt.com",
        "claude.ai",
        "copilot.microsoft.com",
        "gemini.google.com",
        "openai.com",
        "perplexity.ai",
    },
    "social_network": {
        "facebook.com",
        "instagram.com",
        "reddit.com",
        "tiktok.com",
        "twitter.com",
        "x.com",
    },
    "vpn_proxy": {"nordvpn.com", "protonvpn.com", "surfshark.com"},
}


class AuditPolicyEnforcer:
    """Explicit demo adapter that persists policy without changing the OS."""

    def __init__(self, state_path: Path):
        self._state_path = state_path

    def apply(self, payload: dict[str, Any]) -> str:
        specification = parse_policy_payload(payload)
        state = _state_from_specification(specification, mode="audit")
        _write_state(self._state_path, state)
        return specification.policy_hash

    def restore(self) -> None:
        self._state_path.unlink(missing_ok=True)

    def maintain(self) -> None:
        return


class WindowsPolicyEnforcer:
    """Windows adapter for reversible application, network, and USB controls."""

    def __init__(
        self,
        state_path: Path,
        hosts_path: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        self._state_path = state_path
        self._hosts_path = hosts_path or (
            Path(os.getenv("SYSTEMROOT", r"C:\Windows"))
            / "System32"
            / "drivers"
            / "etc"
            / "hosts"
        )
        self._runner = runner

    def apply(self, payload: dict[str, Any]) -> str:
        specification = parse_policy_payload(payload)
        current = self._load_state()
        if (
            current
            and current.get("mode") == "enforce"
            and current.get("policy_hash") == specification.policy_hash
        ):
            self.maintain()
            return specification.policy_hash
        if current:
            if current.get("mode") == "enforce":
                self.restore()
            else:
                self._state_path.unlink(missing_ok=True)

        state = _state_from_specification(specification, mode="enforce")
        state["usb_previous"] = self._read_usb_start() if specification.usb_deny else None
        _write_state(self._state_path, state)
        try:
            self._write_blocked_domains(state["blocked_domains"])
            if specification.usb_deny:
                self._set_usb_start(4)
            self._terminate_denied(list(specification.denied_applications))
        except OSError as exc:
            with suppress(OSError):
                self.restore()
            raise exc
        return specification.policy_hash

    def restore(self) -> None:
        state = self._load_state()
        if state and state.get("mode") == "audit":
            self._state_path.unlink(missing_ok=True)
            return
        self._write_blocked_domains([])
        if state and state.get("usb_deny"):
            previous = state.get("usb_previous")
            self._set_usb_start(previous if isinstance(previous, int) else 3)
        self._state_path.unlink(missing_ok=True)

    def maintain(self) -> None:
        state = self._load_state()
        if state:
            denied = state.get("denied_applications", [])
            if isinstance(denied, list):
                self._terminate_denied(denied)

    def _load_state(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise OSError(f"cannot read EECP policy state: {exc}") from exc
        if not isinstance(value, dict):
            raise OSError("EECP policy state is invalid")
        return value

    def _write_blocked_domains(self, domains: list[str]) -> None:
        try:
            content = self._hosts_path.read_text(encoding="utf-8")
            cleaned = _remove_managed_hosts_block(content).rstrip()
            if domains:
                entries = []
                for domain in sorted(domains):
                    entries.extend((f"127.0.0.1 {domain}", f"127.0.0.1 www.{domain}"))
                block = "\n".join((POLICY_MARKER_START, *entries, POLICY_MARKER_END))
                cleaned = f"{cleaned}\n\n{block}"
            self._hosts_path.write_text(f"{cleaned}\n", encoding="utf-8")
        except OSError as exc:
            raise OSError(
                "cannot update Windows hosts policy; run the Agent as Administrator"
            ) from exc
        self._runner(["ipconfig", "/flushdns"], capture_output=True, text=True, check=False)

    def _read_usb_start(self) -> int:
        result = self._runner(
            ["reg", "query", USBSTOR_KEY, "/v", "Start"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise OSError(
                "cannot read USB storage baseline; run the Agent as Administrator"
            )
        match = re.search(r"REG_DWORD\s+0x([0-9a-f]+)", result.stdout, re.IGNORECASE)
        if match is None:
            raise OSError("cannot parse the current USB storage baseline")
        return int(match.group(1), 16)

    def _set_usb_start(self, value: int) -> None:
        result = self._runner(
            [
                "reg",
                "add",
                USBSTOR_KEY,
                "/v",
                "Start",
                "/t",
                "REG_DWORD",
                "/d",
                str(value),
                "/f",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise OSError(
                "cannot update USB storage policy; run the Agent as Administrator"
            )

    def _terminate_denied(self, applications: list[str]) -> None:
        for application in applications:
            result = self._runner(
                ["taskkill", "/F", "/IM", application],
                capture_output=True,
                text=True,
                check=False,
            )
            output = f"{result.stdout}\n{result.stderr}".lower()
            process_absent = "not found" in output or "no running instance" in output
            if result.returncode != 0 and not process_absent:
                raise OSError(f"cannot terminate denied application: {application}")


def _state_from_specification(
    specification: PolicySpecification, mode: str
) -> dict[str, Any]:
    domains = sorted(
        {
            domain
            for category in specification.blocked_categories
            for domain in CATEGORY_DOMAINS[category]
        }
    )
    return {
        "mode": mode,
        "policy_hash": specification.policy_hash,
        "profile": specification.profile,
        "version": specification.version,
        "denied_applications": list(specification.denied_applications),
        "blocked_domains": domains,
        "usb_deny": specification.usb_deny,
    }


def _remove_managed_hosts_block(content: str) -> str:
    pattern = re.compile(
        rf"\n?{re.escape(POLICY_MARKER_START)}.*?{re.escape(POLICY_MARKER_END)}\n?",
        re.DOTALL,
    )
    return pattern.sub("\n", content)


def _write_state(path: Path, state: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise OSError(f"cannot persist EECP policy state: {exc}") from exc
