import json
import ssl
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.application.policy_commands import PolicyCommandProcessor
from agent.infrastructure.policy_enforcement import (
    POLICY_MARKER_START,
    AuditPolicyEnforcer,
    ProcessIdentity,
    WindowsPolicyEnforcer,
)
from agent.infrastructure.violation_monitor import (
    BlockedDomainMonitor,
    extract_requested_hostname,
)
from contracts.v2 import (
    AckStatus,
    CommandType,
    ErrorCode,
    PolicyRules,
    ServiceResult,
    compute_policy_hash,
)

NOW = datetime(2026, 9, 28, 10, 20, 31, tzinfo=UTC)
RULES = {
    "applications": {"deny": ["AnyDesk.exe"]},
    "network": {"block": ["generative_ai"]},
    "devices": {"usb": "deny"},
}
POLICY_HASH = compute_policy_hash(
    "INTERNET_NO_AI",
    1,
    PolicyRules.model_validate(RULES),
)


def _payload() -> dict:
    return {
        "format": "eecp-policy/v1",
        "policy_hash": POLICY_HASH,
        "version": 1,
        "profile": "INTERNET_NO_AI",
        "rules": RULES,
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

    class Executor:
        maintained = False
        requests = []

        def execute(self, request):
            self.requests.append(request)
            return ServiceResult(
                protocol_version=2,
                request_id=request.request_id,
                command_id=request.command_id,
                status=AckStatus.SUCCEEDED,
                applied_hash=request.policy_hash,
                service_version="test",
                occurred_at=NOW,
                correlation_id=request.correlation_id,
            )

        def maintain(self):
            self.maintained = True

    class Monitor:
        def activate(self, session_id, payload):
            lifecycle.append(("activate", session_id, payload["policy_hash"]))

        def deactivate(self):
            lifecycle.append(("deactivate",))

    executor = Executor()
    PolicyCommandProcessor(
        Client(),
        "PC01",
        executor,
        monitor=Monitor(),
        log=lambda _message: None,
        clock=lambda: NOW,
    ).process_pending()

    assert acknowledgements == [
        (
            "cmd-1",
                {
                    "success": True,
                    "policy_hash": POLICY_HASH,
                    "actor": "PC01",
                    "service_version": "test",
                },
        )
    ]
    assert executor.maintained is False
    assert executor.requests[0].operation == CommandType.APPLY_POLICY
    assert executor.requests[0].payload.policy.policy_hash == POLICY_HASH
    assert lifecycle == [("activate", "ses-1", POLICY_HASH)]


def test_command_processor_restores_and_preserves_ack_semantics() -> None:
    acknowledgements = []
    lifecycle = []

    class Client:
        def pending_commands(self, _agent_id):
            return [
                {
                    "id": "cmd-restore",
                    "session_id": "ses-1",
                    "type": "RESTORE_BASELINE",
                    "payload": {"baseline": "NORMAL"},
                }
            ]

        def acknowledge_command(self, command_id, **values):
            acknowledgements.append((command_id, values))

    class Executor:
        def execute(self, request):
            assert request.operation == CommandType.RESTORE_BASELINE
            return ServiceResult(
                protocol_version=2,
                request_id=request.request_id,
                command_id=request.command_id,
                status=AckStatus.SUCCEEDED,
                service_version="test",
                occurred_at=NOW,
                correlation_id=request.correlation_id,
            )

        def maintain(self):
            return

    class Monitor:
        def activate(self, _session_id, _payload):
            raise AssertionError("restore must not activate monitoring")

        def deactivate(self):
            lifecycle.append("deactivate")

    PolicyCommandProcessor(
        Client(),
        "PC01",
        Executor(),
        monitor=Monitor(),
        log=lambda _message: None,
        clock=lambda: NOW,
    ).process_pending()

    assert acknowledgements == [
        (
            "cmd-restore",
                {
                    "success": True,
                    "policy_hash": None,
                    "actor": "PC01",
                    "service_version": "test",
                },
        )
    ]
    assert lifecycle == ["deactivate"]


def test_command_processor_maps_execution_failure_to_existing_ack() -> None:
    acknowledgements = []

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

    class Executor:
        def execute(self, request):
            return ServiceResult(
                protocol_version=2,
                request_id=request.request_id,
                command_id=request.command_id,
                status=AckStatus.FAILED,
                service_version="test",
                error_code=ErrorCode.EXECUTION_FAILED,
                error_message="access denied",
                occurred_at=NOW,
                correlation_id=request.correlation_id,
            )

        def maintain(self):
            return

    PolicyCommandProcessor(
        Client(),
        "PC01",
        Executor(),
        log=lambda _message: None,
        clock=lambda: NOW,
    ).process_pending()

    assert acknowledgements == [
        (
            "cmd-1",
                {
                    "success": False,
                    "error": "access denied",
                    "actor": "PC01",
                    "service_version": "test",
                },
        )
    ]


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
    denied_process = ProcessIdentity(
        name="AnyDesk.exe",
        pid=4242,
        creation_time="20260928102031.000000+000",
        username=r"LAB\student01",
    )
    running = [denied_process]

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        if arguments[:3] == ["taskkill", "/F", "/PID"]:
            assert arguments[3] == str(denied_process.pid)
            running.clear()
        stdout = "    Start    REG_DWORD    0x3" if arguments[:2] == ["reg", "query"] else ""
        return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

    enforcer = WindowsPolicyEnforcer(
        state_path,
        hosts_path=hosts_path,
        runner=runner,
        process_provider=lambda: tuple(running),
    )

    assert enforcer.apply(_payload()) == POLICY_HASH
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["denied_applications"] == ["anydesk.exe"]
    assert state["usb_previous"] == 3
    assert POLICY_MARKER_START in hosts_path.read_text(encoding="utf-8")
    assert "chatgpt.com" in hosts_path.read_text(encoding="utf-8")
    assert ["taskkill", "/F", "/PID", str(denied_process.pid)] in calls
    assert state["observed_processes"] == [
        {
            "pid": denied_process.pid,
            "creation_time": denied_process.creation_time,
            "outcome": "TERMINATED",
        }
    ]
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


def test_windows_enforcer_writes_valid_domains_and_subdomains_to_hosts(
    tmp_path: Path,
) -> None:
    hosts_path = tmp_path / "hosts"
    baseline = "127.0.0.1 localhost\n::1 localhost\n"
    hosts_path.write_text(baseline, encoding="utf-8")
    state_path = tmp_path / "policy-state.json"

    custom_rules = {
        "applications": {"deny": []},
        "network": {
            "block": [],
            "blocked_domains": ["sub.example.com", "my-domain.org"],
        },
        "devices": {"usb": "allow"},
    }
    custom_hash = compute_policy_hash(
        "CUSTOM", 1, PolicyRules.model_validate(custom_rules)
    )
    payload = {
        "format": "eecp-policy/v1",
        "policy_hash": custom_hash,
        "version": 1,
        "profile": "CUSTOM",
        "rules": custom_rules,
    }

    calls: list[list[str]] = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        stdout = "    Start    REG_DWORD    0x3" if arguments[:2] == ["reg", "query"] else ""
        return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

    enforcer = WindowsPolicyEnforcer(state_path, hosts_path=hosts_path, runner=runner)
    assert enforcer.apply(payload) == custom_hash

    content = hosts_path.read_text(encoding="utf-8")
    assert "127.0.0.1 localhost" in content
    assert "::1 localhost" in content
    assert "127.0.0.1 sub.example.com" in content
    assert "127.0.0.1 www.sub.example.com" in content
    assert "127.0.0.1 my-domain.org" in content
    assert "127.0.0.1 www.my-domain.org" in content

    enforcer.restore()
    restored = hosts_path.read_text(encoding="utf-8")
    assert "sub.example.com" not in restored
    assert "my-domain.org" not in restored
    assert "127.0.0.1 localhost" in restored
    assert "::1 localhost" in restored


@pytest.mark.parametrize(
    "malicious_domain",
    [
        "example.com\n192.168.1.100 evil.com",
        "example.com\r\n192.168.1.100 evil.com",
        "example.com\n# BEGIN EECP MANAGED POLICY",
        "example.com#comment",
        "example.com 192.168.1.100 evil.com",
        "http://example.com",
        "example.com:80",
        "127.0.0.1",
    ],
)
def test_windows_enforcer_rejects_crlf_and_newline_injection(
    tmp_path: Path,
    malicious_domain: str,
) -> None:
    hosts_path = tmp_path / "hosts"
    initial_content = "127.0.0.1 localhost\n# safe system hosts\n"
    hosts_path.write_text(initial_content, encoding="utf-8")
    state_path = tmp_path / "policy-state.json"

    malicious_payload = {
        "format": "eecp-policy/v1",
        "policy_hash": "a" * 64,
        "version": 1,
        "profile": "INJECTION_ATTEMPT",
        "rules": {
            "applications": {"deny": []},
            "network": {"blocked_domains": [malicious_domain]},
            "devices": {"usb": "allow"},
        },
    }

    enforcer = WindowsPolicyEnforcer(
        state_path,
        hosts_path=hosts_path,
        runner=lambda args, **kw: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )

    with pytest.raises(ValueError):
        enforcer.apply(malicious_payload)

    # Hosts file MUST remain strictly identical to initial content
    assert hosts_path.read_text(encoding="utf-8") == initial_content
    assert "evil.com" not in hosts_path.read_text(encoding="utf-8")
    assert not state_path.exists()


def test_write_blocked_domains_defense_in_depth(tmp_path: Path) -> None:
    hosts_path = tmp_path / "hosts"
    initial = "127.0.0.1 localhost\n"
    hosts_path.write_text(initial, encoding="utf-8")
    state_path = tmp_path / "policy-state.json"

    enforcer = WindowsPolicyEnforcer(
        state_path,
        hosts_path=hosts_path,
        runner=lambda args, **kw: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )

    with pytest.raises(ValueError):
        enforcer._write_blocked_domains(["attack.com\n10.0.0.1 evil.corp"])

    assert hosts_path.read_text(encoding="utf-8") == initial
