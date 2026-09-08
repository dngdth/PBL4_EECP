import json
import ssl
import subprocess
from pathlib import Path

import pytest

from agent.application.policy_commands import PolicyCommandProcessor
from agent.infrastructure.policy_enforcement import (
    POLICY_MARKER_START,
    AuditPolicyEnforcer,
    WindowsPolicyEnforcer,
)
from agent.infrastructure.violation_monitor import (
    BlockedDomainMonitor,
    extract_requested_hostname,
)

POLICY_HASH = "a" * 64


def _payload() -> dict:
    return {
        "format": "eecp-policy/v1",
        "policy_hash": POLICY_HASH,
        "version": 1,
        "profile": "INTERNET_NO_AI",
        "rules": {
            "applications": {"deny": ["AnyDesk.exe"]},
            "network": {"block": ["generative_ai"]},
            "devices": {"usb": "deny"},
        },
    }


def test_command_processor_applies_and_acknowledges_policy(tmp_path: Path) -> None:
    acknowledgements = []
    lifecycle = []

    class Client:
        def pending_commands(self, _agent_id):
            return [
                {
                    "id": "cmd-1",
                    "session_id": "ses-1",
                    "type": "APPLY_POLICY",
                    "payload": _payload(),
                }
            ]

        def acknowledge_command(self, command_id, **values):
            acknowledgements.append((command_id, values))

    class Enforcer:
        maintained = False

        def apply(self, payload):
            assert payload["profile"] == "INTERNET_NO_AI"
            return payload["policy_hash"]

        def restore(self):
            raise AssertionError("restore was not requested")

        def maintain(self):
            self.maintained = True

    class Monitor:
        def activate(self, session_id, payload):
            lifecycle.append(("activate", session_id, payload["policy_hash"]))

        def deactivate(self):
            lifecycle.append(("deactivate",))

    enforcer = Enforcer()
    PolicyCommandProcessor(
        Client(),
        "PC01",
        enforcer,
        monitor=Monitor(),
        log=lambda _message: None,
    ).process_pending()

    assert acknowledgements == [
        (
            "cmd-1",
            {
                "success": True,
                "policy_hash": POLICY_HASH,
                "actor": "PC01",
            },
        )
    ]
    assert enforcer.maintained is True
    assert lifecycle == [("activate", "ses-1", POLICY_HASH)]


def test_violation_monitor_reports_blocked_domain_once_per_debounce_window() -> None:
    reports = []
    now = 100.0
    monitor = BlockedDomainMonitor(
        lambda session_id, hostname: reports.append((session_id, hostname)),
        log=lambda _message: None,
        clock=lambda: now,
    )
    monitor.activate("ses-1", _payload())

    monitor.record_attempt("chatgpt.com")
    monitor.record_attempt("chatgpt.com")
    monitor.record_attempt("example.com")
    now += 16
    monitor.record_attempt("chatgpt.com")

    assert reports == [
        ("ses-1", "chatgpt.com"),
        ("ses-1", "chatgpt.com"),
    ]


def test_violation_monitor_extracts_http_host() -> None:
    request = b"GET / HTTP/1.1\r\nHost: chatgpt.com\r\nConnection: close\r\n\r\n"

    assert extract_requested_hostname(request) == "chatgpt.com"


def test_violation_monitor_extracts_tls_sni() -> None:
    incoming = ssl.MemoryBIO()
    outgoing = ssl.MemoryBIO()
    connection = ssl.create_default_context().wrap_bio(
        incoming,
        outgoing,
        server_side=False,
        server_hostname="chatgpt.com",
    )
    with pytest.raises(ssl.SSLWantReadError):
        connection.do_handshake()

    assert extract_requested_hostname(outgoing.read()) == "chatgpt.com"


def test_windows_enforcer_applies_reversible_controls(tmp_path: Path) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    state_path = tmp_path / "policy-state.json"
    calls: list[list[str]] = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        stdout = "    Start    REG_DWORD    0x3" if arguments[:2] == ["reg", "query"] else ""
        return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

    enforcer = WindowsPolicyEnforcer(state_path, hosts_path=hosts_path, runner=runner)

    assert enforcer.apply(_payload()) == POLICY_HASH
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["denied_applications"] == ["anydesk.exe"]
    assert state["usb_previous"] == 3
    assert POLICY_MARKER_START in hosts_path.read_text(encoding="utf-8")
    assert "chatgpt.com" in hosts_path.read_text(encoding="utf-8")
    assert ["taskkill", "/F", "/IM", "anydesk.exe"] in calls
    assert any(call[:2] == ["reg", "add"] and "4" in call for call in calls)

    enforcer.restore()

    assert not state_path.exists()
    assert POLICY_MARKER_START not in hosts_path.read_text(encoding="utf-8")
    assert any(call[:2] == ["reg", "add"] and "3" in call for call in calls)


def test_switching_from_audit_to_enforce_does_not_restore_unmodified_usb(
    tmp_path: Path,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    state_path = tmp_path / "policy-state.json"
    AuditPolicyEnforcer(state_path).apply(_payload())
    calls: list[list[str]] = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        stdout = "    Start    REG_DWORD    0x3" if arguments[:2] == ["reg", "query"] else ""
        return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

    WindowsPolicyEnforcer(state_path, hosts_path=hosts_path, runner=runner).apply(
        _payload()
    )

    usb_writes = [call for call in calls if call[:2] == ["reg", "add"]]
    assert len(usb_writes) == 1
    assert "4" in usb_writes[0]
