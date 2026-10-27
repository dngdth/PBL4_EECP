from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.domain.policy import (
    CATEGORY_DOMAINS,
    PolicySpecification,
    parse_policy_payload,
)
from agent.infrastructure.windows.firewall_enforcer import (
    FirewallEnforcer,
    FirewallRule,
    WindowsFirewallEnforcer,
)
from contracts.v2.policy import validate_hostname

POLICY_MARKER_START = "# BEGIN EECP MANAGED POLICY"
POLICY_MARKER_END = "# END EECP MANAGED POLICY"
USBSTOR_KEY = r"HKLM\SYSTEM\CurrentControlSet\Services\USBSTOR"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    name: str
    pid: int
    creation_time: str
    username: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessEnforcementResult:
    process: ProcessIdentity
    detected: bool
    termination_attempted: bool
    outcome: str
    error: str | None = None


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
        event_sink: Callable[..., None] | None = None,
        process_provider: Callable[[], tuple[ProcessIdentity, ...]] | None = None,
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
        self._event_sink = event_sink
        self._process_provider = process_provider or self._windows_processes
        self._operation_lock = threading.RLock()
        self._seen_processes: set[tuple[int, str]] = set()
        self._process_outcomes: dict[tuple[int, str], str] = {}
        self._degraded_error: str | None = None

    def set_event_sink(self, sink: Callable[..., None]) -> None:
        self._event_sink = sink

    @property
    def health_error(self) -> str | None:
        return self._degraded_error

    def apply(self, payload: dict[str, Any]) -> str:
        with self._operation_lock:
            return self._apply_locked(payload)

    def _apply_locked(self, payload: dict[str, Any]) -> str:
        self._degraded_error = None
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
        self._seen_processes.clear()
        self._process_outcomes.clear()
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
            self._terminate_denied(
                list(specification.denied_applications),
                session_id=specification.session_id,
            )
            self._store_process_observations(state)
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
        with self._operation_lock:
            self._restore_locked()

    def _restore_locked(self) -> None:
        self._degraded_error = None
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
        self._seen_processes.clear()
        self._process_outcomes.clear()

    def maintain(self) -> None:
        with self._operation_lock:
            self._maintain_locked()

    def _maintain_locked(self) -> None:
        self._degraded_error = None
        state = self._load_state()
        if state:
            if state.get("mode") != "enforce":
                return
            session_id = str(state.get("session_id", ""))
            self._load_process_observations(state)
            rules = self._rules_from_state(state)
            if not self._firewall.verify_rules(rules):
                self._record(
                    session_id,
                    "ENFORCEMENT_TAMPER",
                    "FIREWALL_INTEGRITY",
                    "CONFIG_CHANGED",
                    "WARNING",
                    target="windows-firewall",
                )
                try:
                    self._firewall.reconcile(rules, self._managed_rule_names(state))
                    recovered = self._firewall.verify_rules(rules)
                except (OSError, ValueError) as exc:
                    recovered = False
                    error = str(exc)[:500]
                else:
                    error = None
                self._record(
                    session_id,
                    "ENFORCEMENT_TAMPER",
                    "FIREWALL_INTEGRITY",
                    "RECOVERED" if recovered else "RECOVERY_FAILED",
                    "WARNING" if recovered else "CRITICAL",
                    target="windows-firewall",
                    recovery_status="RECOVERY_SUCCEEDED" if recovered else "RECOVERY_FAILED",
                    error=error,
                )
                if not recovered:
                    self._degraded_error = error or "Firewall recovery verification failed"
            expected_domains = _state_string_list(state, "blocked_domains")
            if not self._hosts_match(expected_domains):
                self._recover_hosts(session_id, expected_domains)
            if bool(state.get("usb_deny")) and self._read_usb_start() != 4:
                self._recover_usb(session_id)
            denied = state.get("denied_applications", [])
            if isinstance(denied, list):
                self._terminate_denied(denied, session_id=session_id)
                self._store_process_observations(state)
                _write_state(self._state_path, state)

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
        for domain in domains:
            validate_hostname(domain, field="hosts domain")
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

    def _terminate_denied(
        self, applications: list[str], *, session_id: str | None = None
    ) -> tuple[ProcessEnforcementResult, ...]:
        denied = {application.casefold() for application in applications}
        running = self._process_provider()
        active_identities = {(item.pid, item.creation_time) for item in running}
        self._seen_processes.intersection_update(active_identities)
        self._process_outcomes = {
            identity: outcome
            for identity, outcome in self._process_outcomes.items()
            if identity in active_identities
        }
        results: list[ProcessEnforcementResult] = []
        if session_id is None:
            state = self._load_state()
            session_id = str(state.get("session_id", "")) if state else ""
        for process in running:
            identity = (process.pid, process.creation_time)
            if process.name.casefold() not in denied:
                continue
            is_new = identity not in self._seen_processes
            if is_new:
                self._seen_processes.add(identity)
                self._record(
                    session_id,
                    "FORBIDDEN_PROCESS_DETECTED",
                    "FORBIDDEN_PROCESS",
                    "DETECTED",
                    "WARNING",
                    process_name=process.name,
                    pid=process.pid,
                    username=process.username,
                    process_creation_time=process.creation_time,
                )
            confirmed = any(
                candidate.pid == process.pid
                and candidate.creation_time == process.creation_time
                for candidate in self._process_provider()
            )
            if not confirmed:
                enforcement = ProcessEnforcementResult(
                    process=process,
                    detected=True,
                    termination_attempted=False,
                    outcome="ALREADY_EXITED",
                )
                results.append(enforcement)
                if self._process_outcomes.get(identity) != "ALREADY_EXITED":
                    self._record(
                        session_id,
                        "ENFORCEMENT_ACTION",
                        "FORBIDDEN_PROCESS",
                        "ALREADY_EXITED",
                        "WARNING",
                        process_name=process.name,
                        pid=process.pid,
                        username=process.username,
                        process_creation_time=process.creation_time,
                        enforcement_result="ALREADY_EXITED",
                    )
                self._process_outcomes[identity] = "ALREADY_EXITED"
                continue
            result = self._runner(
                ["taskkill", "/F", "/PID", str(process.pid)],
                capture_output=True,
                text=True,
                check=False,
            )
            still_running = any(
                candidate.pid == process.pid
                and candidate.creation_time == process.creation_time
                for candidate in self._process_provider()
            )
            if not still_running:
                outcome = "TERMINATED" if result.returncode == 0 else "ALREADY_EXITED"
            else:
                outcome = "FAILED"
            enforcement = ProcessEnforcementResult(
                process=process,
                detected=True,
                termination_attempted=True,
                outcome=outcome,
                error=(None if outcome != "FAILED" else "process remained active"),
            )
            results.append(enforcement)
            if self._process_outcomes.get(identity) != outcome:
                self._record(
                    session_id,
                    "ENFORCEMENT_ACTION",
                    "FORBIDDEN_PROCESS",
                    "BLOCKED" if outcome == "TERMINATED" else outcome,
                    "WARNING" if outcome != "FAILED" else "CRITICAL",
                    process_name=process.name,
                    pid=process.pid,
                    username=process.username,
                    process_creation_time=process.creation_time,
                    enforcement_result=outcome,
                )
            self._process_outcomes[identity] = outcome
            if outcome == "FAILED":
                self._degraded_error = (
                    f"denied process remained active: {process.name} pid={process.pid}"
                )
        return tuple(results)

    def _load_process_observations(self, state: dict[str, Any]) -> None:
        values = state.get("observed_processes", [])
        if not isinstance(values, list):
            return
        for value in values:
            if not isinstance(value, dict):
                continue
            pid = value.get("pid")
            creation_time = value.get("creation_time")
            outcome = value.get("outcome")
            if isinstance(pid, int) and isinstance(creation_time, str):
                identity = (pid, creation_time)
                self._seen_processes.add(identity)
                if isinstance(outcome, str):
                    self._process_outcomes[identity] = outcome

    def _store_process_observations(self, state: dict[str, Any]) -> None:
        state["observed_processes"] = [
            {
                "pid": pid,
                "creation_time": creation_time,
                "outcome": self._process_outcomes.get((pid, creation_time)),
            }
            for pid, creation_time in sorted(self._seen_processes)
        ]

    def _windows_processes(self) -> tuple[ProcessIdentity, ...]:
        script = (
            "@(Get-CimInstance Win32_Process | ForEach-Object { "
            "$owner=Invoke-CimMethod -InputObject $_ -MethodName GetOwner; "
            "[PSCustomObject]@{Name=$_.Name;Pid=$_.ProcessId;"
            "CreationTime=$_.CreationDate;Username=if($owner.ReturnValue -eq 0)"
            "{$owner.Domain+'\\'+$owner.User}else{$null}} }) | ConvertTo-Json -Compress"
        )
        result = self._runner(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise OSError("cannot enumerate Windows processes")
        try:
            raw = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise OSError("Windows process inventory returned invalid data") from exc
        values = raw if isinstance(raw, list) else [raw]
        return tuple(
            ProcessIdentity(
                name=str(item["Name"]).lower(),
                pid=int(item["Pid"]),
                creation_time=str(item["CreationTime"]),
                username=(str(item["Username"]) if item.get("Username") else None),
            )
            for item in values
            if isinstance(item, dict)
            and item.get("Name")
            and item.get("Pid") is not None
            and item.get("CreationTime")
        )

    def _hosts_match(self, expected_domains: tuple[str, ...]) -> bool:
        content = self._read_hosts()
        expected = {
            line
            for domain in expected_domains
            for line in (f"127.0.0.1 {domain}", f"127.0.0.1 www.{domain}")
        }
        match = re.search(
            rf"{re.escape(POLICY_MARKER_START)}\s*(.*?)\s*{re.escape(POLICY_MARKER_END)}",
            content,
            re.DOTALL,
        )
        if match is None:
            return not expected
        actual = {line.strip() for line in match.group(1).splitlines() if line.strip()}
        return actual == expected

    def _recover_hosts(self, session_id: str, domains: tuple[str, ...]) -> None:
        self._record(
            session_id, "ENFORCEMENT_TAMPER", "HOSTS_INTEGRITY", "CONFIG_CHANGED",
            "WARNING", target=str(self._hosts_path)
        )
        try:
            self._write_blocked_domains(list(domains))
            recovered = self._hosts_match(domains)
        except OSError as exc:
            recovered, error = False, str(exc)[:500]
        else:
            error = None
        self._record(
            session_id, "ENFORCEMENT_TAMPER", "HOSTS_INTEGRITY",
            "RECOVERED" if recovered else "RECOVERY_FAILED",
            "WARNING" if recovered else "CRITICAL", target=str(self._hosts_path),
            recovery_status="RECOVERY_SUCCEEDED" if recovered else "RECOVERY_FAILED",
            error=error,
        )
        if not recovered:
            self._degraded_error = error or "hosts recovery verification failed"

    def _recover_usb(self, session_id: str) -> None:
        self._record(
            session_id, "ENFORCEMENT_TAMPER", "USB_INTEGRITY", "CONFIG_CHANGED",
            "WARNING", target=USBSTOR_KEY
        )
        try:
            self._set_usb_start(4)
            recovered = self._read_usb_start() == 4
        except OSError as exc:
            recovered, error = False, str(exc)[:500]
        else:
            error = None
        self._record(
            session_id, "ENFORCEMENT_TAMPER", "USB_INTEGRITY",
            "RECOVERED" if recovered else "RECOVERY_FAILED",
            "WARNING" if recovered else "CRITICAL", target=USBSTOR_KEY,
            recovery_status="RECOVERY_SUCCEEDED" if recovered else "RECOVERY_FAILED",
            error=error,
        )
        if not recovered:
            self._degraded_error = error or "USB recovery verification failed"

    def _record(
        self,
        session_id: str,
        event_type: str,
        category: str,
        action: str,
        severity: str,
        **details: Any,
    ) -> None:
        if self._event_sink is None or not session_id:
            return
        try:
            self._event_sink(
                session_id=session_id,
                event_type=event_type,
                category=category,
                action=action,
                severity=severity,
                source="privileged-service",
                details={key: value for key, value in details.items() if value is not None},
            )
        except Exception as exc:
            self._degraded_error = "security event journal unavailable"
            LOGGER.error("security event journal write failed: %s", str(exc)[:500])


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
