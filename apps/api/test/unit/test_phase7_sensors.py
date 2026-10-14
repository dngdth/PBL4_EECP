from datetime import UTC, datetime

from agent.client.sensors.process_sensor import ProcessSensor
from agent.client.sensors.service_health_sensor import ServiceHealthSensor
from agent.client.sensors.suite import SensorSuite
from contracts.v2 import (
    AckStatus,
    ErrorCode,
    PolicyRules,
    ServiceHealth,
    ServiceResult,
    compute_policy_hash,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _policy() -> dict:
    rules = {"applications": {"deny": ["anydesk.exe"]}}
    return {
        "format": "eecp-policy/v1",
        "profile": "PROCESS_TEST",
        "version": 1,
        "policy_hash": compute_policy_hash(
            "PROCESS_TEST", 1, PolicyRules.model_validate(rules)
        ),
        "rules": rules,
    }


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
