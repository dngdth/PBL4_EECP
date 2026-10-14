from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from agent.domain.policy import PolicySpecification, parse_policy_payload
from agent.infrastructure.windows.firewall_enforcer import (
    FirewallEnforcer,
    FirewallRule,
    WindowsFirewallEnforcer,
)

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
LOGGER = logging.getLogger(__name__)


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
        firewall: FirewallEnforcer | None = None,
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
        self._firewall = firewall or WindowsFirewallEnforcer(runner=runner)

    def apply(self, payload: dict[str, Any]) -> str:
        specification = parse_policy_payload(payload)
        current = self._load_state()
        desired_rules = self._firewall.plan(
            specification.session_id,
            specification.blocked_ips,
            specification.blocked_cidrs,
        )
        LOGGER.info(
            "policy operation=apply session_id=%s policy_hash=%s firewall_rules=%d",
            specification.session_id,
            specification.policy_hash,
            len(desired_rules),
        )
        if (
            current
            and current.get("mode") == "enforce"
            and current.get("policy_hash") == specification.policy_hash
            and "managed_firewall_rules" in current
        ):
            self.maintain()
            return specification.policy_hash
        state = _state_from_specification(specification, mode="enforce")
        state["managed_firewall_rules"] = [rule.name for rule in desired_rules]
        previous_usb_deny = bool(
            current and current.get("mode") == "enforce" and current.get("usb_deny")
        )
        usb_before = None
        if previous_usb_deny != specification.usb_deny:
            usb_before = self._read_usb_start()
        if specification.usb_deny and previous_usb_deny:
            state["usb_previous"] = current.get("usb_previous")
        elif specification.usb_deny:
            state["usb_previous"] = usb_before
        else:
            state["usb_previous"] = None
        previous_hosts = self._read_hosts()
        previous_rules = self._rules_from_state(current)
        current_names = self._managed_rule_names(current)
        try:
            self._write_blocked_domains(state["blocked_domains"])
            self._firewall.reconcile(desired_rules, current_names)
            if specification.usb_deny and not previous_usb_deny:
                self._set_usb_start(4)
            elif previous_usb_deny and not specification.usb_deny:
                previous = current.get("usb_previous") if current else None
                self._set_usb_start(previous if isinstance(previous, int) else 3)
            self._terminate_denied(list(specification.denied_applications))
            _write_state(self._state_path, state)
            LOGGER.info(
                "policy operation=apply session_id=%s policy_hash=%s result=succeeded",
                specification.session_id,
                specification.policy_hash,
            )
        except (OSError, ValueError) as exc:
            with suppress(OSError, ValueError):
                self._write_hosts(previous_hosts)
            with suppress(OSError, ValueError):
                self._firewall.reconcile(
                    previous_rules,
                    tuple(rule.name for rule in desired_rules),
                )
            if usb_before is not None:
                with suppress(OSError):
                    self._set_usb_start(usb_before)
            LOGGER.error(
                "policy operation=apply session_id=%s policy_hash=%s result=failed",
                specification.session_id,
                specification.policy_hash,
            )
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
        self._firewall.remove_rules(self._managed_rule_names(state))
        self._state_path.unlink(missing_ok=True)

    def maintain(self) -> None:
        state = self._load_state()
        if state:
            if state.get("mode") != "enforce":
                return
            rules = self._rules_from_state(state)
            self._firewall.reconcile(rules, self._managed_rule_names(state))
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

    def _read_hosts(self) -> str:
        try:
            return self._hosts_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError("cannot read Windows hosts policy") from exc

    def _write_hosts(self, content: str) -> None:
        try:
            self._hosts_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise OSError("cannot restore Windows hosts policy") from exc
        self._runner(["ipconfig", "/flushdns"], capture_output=True, text=True, check=False)

    @staticmethod
    def _managed_rule_names(state: dict[str, Any] | None) -> tuple[str, ...]:
        values = state.get("managed_firewall_rules", []) if state else []
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise OSError("EECP managed Firewall state is invalid")
        return tuple(values)

    def _rules_from_state(self, state: dict[str, Any] | None) -> tuple[FirewallRule, ...]:
        if not state or state.get("mode") != "enforce":
            return ()
        return self._firewall.plan(
            str(state.get("session_id", "")),
            _state_string_list(state, "blocked_ips"),
            _state_string_list(state, "blocked_cidrs"),
        )

    def _write_blocked_domains(self, domains: list[str]) -> None:
        try:
            content = self._read_hosts()
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
        | set(specification.blocked_domains)
    )
    return {
        "mode": mode,
        "policy_hash": specification.policy_hash,
        "profile": specification.profile,
        "version": specification.version,
        "session_id": specification.session_id,
        "denied_applications": list(specification.denied_applications),
        "blocked_domains": domains,
        "blocked_ips": list(specification.blocked_ips),
        "blocked_cidrs": list(specification.blocked_cidrs),
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


def _state_string_list(state: dict[str, Any], key: str) -> tuple[str, ...]:
    values = state.get(key, [])
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise OSError(f"EECP policy state field {key} is invalid")
    return tuple(values)
