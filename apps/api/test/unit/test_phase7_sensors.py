from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agent.client import main as client_main
from agent.client.sensors import dns_audit_sensor as dns_audit_module
from agent.client.sensors.dns_audit_sensor import (
    DNS_CACHE_COMMAND,
    DnsAuditSensor,
    windows_dns_cache_names,
)
from agent.client.sensors.process_sensor import ProcessSensor
from agent.client.sensors.service_health_sensor import ServiceHealthSensor
from agent.client.sensors.suite import SensorSuite
from agent.domain.identity import WorkstationIdentity
from contracts.v2 import (
    AckStatus,
    ErrorCode,
    PolicyRules,
    ServiceHealth,
    ServiceResult,
    compute_policy_hash,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _policy(rules: dict | None = None) -> dict:
    rules = rules or {"applications": {"deny": ["anydesk.exe"]}}
    return {
        "format": "eecp-policy/v1",
        "profile": "PROCESS_TEST",
        "version": 1,
        "policy_hash": compute_policy_hash(
            "PROCESS_TEST", 1, PolicyRules.model_validate(rules)
        ),
        "rules": rules,
    }


@pytest.mark.parametrize(
    ("policy_mode", "expect_dns_sensor"),
    [("audit", True), ("enforce", False)],
)
def test_client_builds_dns_sensor_only_for_audit_mode(
    monkeypatch: pytest.MonkeyPatch,
    policy_mode: str,
    expect_dns_sensor: bool,
) -> None:
    captured: list[tuple[object, ...]] = []

    class Backend:
        def report_policy_violation(self, *_args):
            return None

        def report_event(self, *_args):
            return None

    class Monitor:
        def __init__(self, *_args):
            return

        def start(self):
            return

    class FakeSuite:
        def __init__(self, sensors, **_kwargs):
            captured.append(sensors)

    monkeypatch.setattr(client_main, "POLICY_MODE", policy_mode)
    monkeypatch.setattr(
        client_main,
        "collect_identity",
        lambda *_args: WorkstationIdentity(
            "AGT-001", "HOST", "192.0.2.1", "1.1.0"
        ),
    )
    monkeypatch.setattr(client_main, "BlockedDomainMonitor", Monitor)
    monkeypatch.setattr(client_main, "ProcessSensor", lambda *_args: object())
    monkeypatch.setattr(client_main, "ServiceHealthSensor", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(client_main, "SensorSuite", FakeSuite)

    client_main.build_client_runtime("AGT-001", object(), Backend())

    has_dns_sensor = any(
        isinstance(sensor, DnsAuditSensor) for sensor in captured[0]
    )
    assert has_dns_sensor is expect_dns_sensor


def test_dns_audit_sensor_reports_blocked_domain_and_debounces() -> None:
    now = 0.0
    reports: list[tuple[str, str]] = []
    sensor = DnsAuditSensor(
        lambda session_id, hostname: reports.append((session_id, hostname)),
        dns_provider=lambda: ("API.ChatGPT.com.", "allowed.example"),
        clock=lambda: now,
        debounce_seconds=15,
    )
    sensor.activate(
        "SES-1",
        _policy({"network": {"block": ["generative_ai"]}}),
    )

    sensor.poll()
    sensor.poll()
    now = 15
    sensor.poll()

    assert reports == [
        ("SES-1", "chatgpt.com"),
        ("SES-1", "chatgpt.com"),
    ]


def test_dns_audit_sensor_ignores_unlisted_domain_and_matches_subdomain() -> None:
    reports: list[tuple[str, str]] = []
    sensor = DnsAuditSensor(
        lambda session_id, hostname: reports.append((session_id, hostname)),
        dns_provider=lambda: ("allowed.example", "cdn.chatgpt.com"),
    )
    sensor.activate(
        "SES-1",
        _policy({"network": {"block": ["generative_ai"]}}),
    )

    sensor.poll()

    assert reports == [("SES-1", "chatgpt.com")]


def test_dns_cache_reader_uses_fixed_argument_list_without_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout='["chatgpt.com","evil.example; Remove-Item C:\\\\*"]',
            stderr="",
        )

    monkeypatch.setattr(dns_audit_module.sys, "platform", "win32")

    names = windows_dns_cache_names(runner=runner)

    assert names[0] == "chatgpt.com"
    assert calls == [
        (
            list(DNS_CACHE_COMMAND),
            {
                "capture_output": True,
                "text": True,
                "check": False,
                "timeout": 5,
            },
        )
    ]
    assert "shell" not in calls[0][1]


def test_dns_cache_reader_rejects_powershell_non_terminating_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dns_audit_module.sys, "platform", "win32")

    with pytest.raises(OSError, match="provider command failed"):
        windows_dns_cache_names(
            runner=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="Get-DnsClientCache: Access denied",
            )
        )


def test_dns_cache_reader_is_safe_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dns_audit_module.sys, "platform", "linux")

    assert windows_dns_cache_names(
        runner=lambda *_args, **_kwargs: pytest.fail("runner must not be called")
    ) == ()


def test_process_sensor_emits_on_presence_transition_without_spam() -> None:
    running: list[str] = []
    detected: list[tuple[str, str]] = []
    sensor = ProcessSensor(
        lambda session_id, process: detected.append((session_id, process)),
        process_provider=lambda: running,
    )
    sensor.activate("SES-1", _policy())

    sensor.poll()
    running.append("AnyDesk.exe")
    sensor.poll()
    sensor.poll()
    running.clear()
    sensor.poll()
    running.append("anydesk.exe")
    sensor.poll()

    assert detected == [("SES-1", "anydesk.exe"), ("SES-1", "anydesk.exe")]


class HealthExecutor:
    def __init__(self, results):
        self.results = iter(results)

    def execute(self, request):
        status, applied_hash = next(self.results)
        values = {
            "protocol_version": 2,
            "request_id": request.request_id,
            "command_id": request.command_id,
            "status": status,
            "service_version": "2.0.0",
            "occurred_at": NOW,
            "correlation_id": request.correlation_id,
            "applied_hash": applied_hash,
        }
        if status != AckStatus.SUCCEEDED:
            values.update(
                error_code=ErrorCode.EXECUTION_FAILED,
                error_message="pipe unavailable",
            )
        return ServiceResult.model_validate(values)


def test_service_health_emits_transitions_once_and_recovers() -> None:
    now = 0.0
    health_events = []
    integrity_events = []
    expected = _policy()["policy_hash"]
    sensor = ServiceHealthSensor(
        HealthExecutor(
            [
                (AckStatus.FAILED, None),
                (AckStatus.FAILED, None),
                (AckStatus.SUCCEEDED, expected),
            ]
        ),
        lambda session_id, state: health_events.append((session_id, state)),
        lambda *values: integrity_events.append(values),
        interval_seconds=5,
        clock=lambda: now,
        utc_clock=lambda: NOW,
    )
    sensor.activate("SES-1", _policy())

    sensor.poll()
    now = 5
    sensor.poll()
    now = 10
    sensor.poll()

    assert health_events == [("SES-1", "UNAVAILABLE"), ("SES-1", "RECOVERED")]
    assert integrity_events == []
    assert sensor.service_health == ServiceHealth.HEALTHY
    assert sensor.active_policy_hash == expected


def test_policy_integrity_mismatch_is_detection_only_and_debounced() -> None:
    now = 0.0
    events = []
    expected = _policy()["policy_hash"]
    actual = "b" * 64
    executor = HealthExecutor(
        [(AckStatus.SUCCEEDED, actual), (AckStatus.SUCCEEDED, actual)]
    )
    sensor = ServiceHealthSensor(
        executor,
        lambda *_args: None,
        lambda *values: events.append(values),
        interval_seconds=5,
        clock=lambda: now,
        utc_clock=lambda: NOW,
    )
    sensor.activate("SES-1", _policy())

    sensor.poll()
    now = 5
    sensor.poll()

    assert events == [("SES-1", expected, actual)]
    assert sensor.service_health == ServiceHealth.DEGRADED


def test_sensor_failure_is_logged_and_does_not_stop_other_sensors() -> None:
    calls: list[str] = []

    class BrokenSensor:
        def activate(self, _session_id, _payload):
            return

        def deactivate(self):
            return

        def poll(self):
            raise OSError("provider unavailable")

    class HealthySensor(BrokenSensor):
        def poll(self):
            calls.append("healthy")

    suite = SensorSuite(
        (BrokenSensor(), HealthySensor()),
        log=lambda message: calls.append(message),
    )

    suite.poll()

    assert calls == [
        "Sensor BrokenSensor failed: provider unavailable",
        "healthy",
    ]


def test_dns_provider_failure_does_not_stop_other_sensors() -> None:
    calls: list[str] = []
    dns = DnsAuditSensor(
        lambda *_args: pytest.fail("a failed provider cannot report"),
        dns_provider=lambda: (_ for _ in ()).throw(OSError("DNS cache denied")),
        log=lambda message: calls.append(message),
    )
    dns.activate("SES-1", _policy({"network": {"block": ["generative_ai"]}}))

    class HealthySensor:
        def activate(self, _session_id, _payload):
            return

        def deactivate(self):
            return

        def poll(self):
            calls.append("healthy")

    suite = SensorSuite((dns, HealthySensor()), log=lambda message: calls.append(message))
    suite.poll()

    assert any("Sensor DnsAuditSensor failed: DNS cache denied" in value for value in calls)
    assert calls[-1] == "healthy"


def test_service_health_deactivation_clears_ephemeral_state() -> None:
    expected = _policy()["policy_hash"]
    sensor = ServiceHealthSensor(
        HealthExecutor([(AckStatus.SUCCEEDED, expected)]),
        lambda *_args: None,
        lambda *_args: None,
        interval_seconds=5,
        clock=lambda: 0,
    )
    sensor.activate("SES-1", _policy())
    sensor.poll()

    sensor.deactivate()

    assert sensor.service_health == ServiceHealth.HEALTHY
    assert sensor.active_policy_hash is None
