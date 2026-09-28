from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.client import main as client_main
from agent.client.runtime import AgentClientRuntime
from agent.domain.identity import WorkstationIdentity
from agent.service import main as service_main
from agent.service.application.execution_service import ExecutionService
from agent.service.runtime import AgentServiceRuntime
from contracts.v2 import (
    AckStatus,
    ApplyPolicyPayload,
    CommandType,
    ErrorCode,
    HealthCheckPayload,
    PolicyEnvelope,
    PolicyRules,
    RestoreBaselinePayload,
    ServiceRequest,
    ServiceResult,
    compute_policy_hash,
)

NOW = datetime(2026, 9, 28, 10, 20, 31, tzinfo=UTC)


def _request(operation: CommandType) -> ServiceRequest:
    policy_hash = None
    if operation == CommandType.APPLY_POLICY:
        rules = PolicyRules()
        policy_hash = compute_policy_hash("DEFAULT", 1, rules)
        payload = ApplyPolicyPayload(
            policy=PolicyEnvelope(
                protocol_version=2,
                policy_id="DEFAULT",
                policy_version=1,
                policy_hash=policy_hash,
                session_id="ses-1",
                issued_at=NOW,
                rules=rules,
            )
        )
    elif operation == CommandType.RESTORE_BASELINE:
        payload = RestoreBaselinePayload(baseline="NORMAL")
    else:
        payload = HealthCheckPayload(nonce="check-1")
    return ServiceRequest.model_construct(
        protocol_version=2,
        request_id=f"req-{operation.value}",
        command_id=f"cmd-{operation.value}",
        operation=operation,
        session_id="ses-1",
        policy_hash=policy_hash,
        payload=payload,
        correlation_id="corr-1",
    )


def _result(request: ServiceRequest, status: AckStatus) -> ServiceResult:
    values = {
        "protocol_version": 2,
        "request_id": request.request_id,
        "command_id": request.command_id,
        "status": status,
        "service_version": "1.1.0",
        "occurred_at": NOW,
        "correlation_id": request.correlation_id,
    }
    if status != AckStatus.SUCCEEDED:
        values["error_code"] = ErrorCode.EXECUTION_FAILED
        values["error_message"] = "failed"
    return ServiceResult.model_validate(values)


@pytest.mark.parametrize("operation", list(CommandType))
def test_execution_service_delegates_allowlisted_requests(operation: CommandType) -> None:
    request = _request(operation)
    expected = _result(request, AckStatus.SUCCEEDED)

    class Executor:
        received = []

        def execute(self, value):
            self.received.append(value)
            return expected

        def maintain(self):
            return

    executor = Executor()

    assert ExecutionService(executor).handle(request) is expected
    assert executor.received == [request]


def test_execution_service_preserves_failure_result() -> None:
    request = _request(CommandType.RESTORE_BASELINE)
    expected = _result(request, AckStatus.FAILED)

    class Executor:
        def execute(self, _request):
            return expected

        def maintain(self):
            return

    assert ExecutionService(Executor()).handle(request) is expected


def test_execution_service_preserves_unsupported_rejection() -> None:
    request = _request(CommandType.HEALTH_CHECK).model_copy(
        update={"operation": "RUN_SHELL"}
    )
    expected = ServiceResult(
        protocol_version=2,
        request_id=request.request_id,
        command_id=request.command_id,
        status=AckStatus.REJECTED,
        service_version="1.1.0",
        error_code=ErrorCode.UNSUPPORTED_OPERATION,
        error_message="unsupported operation",
        occurred_at=NOW,
        correlation_id=request.correlation_id,
    )

    class Executor:
        def execute(self, _request):
            return expected

        def maintain(self):
            return

    result = ExecutionService(Executor(), clock=lambda: NOW).handle(request)

    assert result.status == AckStatus.REJECTED
    assert result.error_code == ErrorCode.UNSUPPORTED_OPERATION


class FakeEnforcer:
    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.maintained = 0

    def apply(self, _payload):
        return "a" * 64

    def restore(self):
        return

    def maintain(self):
        self.maintained += 1


@pytest.mark.parametrize("mode", ["audit", "enforce"])
def test_service_runtime_owns_selected_enforcer_and_maintenance(
    mode: str,
    tmp_path: Path,
) -> None:
    audit_instances = []
    windows_instances = []

    def audit_factory(path):
        instance = FakeEnforcer(path)
        audit_instances.append(instance)
        return instance

    def windows_factory(path):
        instance = FakeEnforcer(path)
        windows_instances.append(instance)
        return instance

    runtime = AgentServiceRuntime.build(
        policy_mode=mode,
        state_path=tmp_path / "state.json",
        service_version="1.1.0",
        audit_factory=audit_factory,
        windows_factory=windows_factory,
    )
    runtime.maintain_once()

    selected = audit_instances if mode == "audit" else windows_instances
    unselected = windows_instances if mode == "audit" else audit_instances
    assert runtime.enforcer is selected[0]
    assert selected[0].maintained == 1
    assert unselected == []
    assert isinstance(runtime.execution_service, ExecutionService)


def test_service_runtime_rejects_unknown_mode_without_constructing_enforcer(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="EECP_POLICY_MODE"):
        AgentServiceRuntime.build(
            policy_mode="unknown",
            state_path=tmp_path / "state.json",
            service_version="1.1.0",
            audit_factory=lambda _path: pytest.fail("must not construct audit enforcer"),
            windows_factory=lambda _path: pytest.fail("must not construct Windows enforcer"),
        )


def test_client_runtime_keeps_registration_heartbeat_and_monitoring_flow() -> None:
    calls = []

    class Backend:
        def register(self, identity):
            calls.append(("register", identity.agent_id))

        def heartbeat(self, agent_id):
            calls.append(("heartbeat", agent_id))

        def active_policy(self, _agent_id):
            calls.append(("active_policy",))
            return None

    class Processor:
        def process_pending(self):
            calls.append(("process_pending",))

    class Monitor:
        def activate(self, _session_id, _payload):
            calls.append(("activate",))

        def deactivate(self):
            calls.append(("deactivate",))

    sleeps = 0

    def stop_after_two_cycles(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    runtime = AgentClientRuntime(
        Backend(),
        WorkstationIdentity("PC01", "HOST", "192.0.2.1", "1.1.0"),
        Processor(),
        Monitor(),
        heartbeat_interval_seconds=5,
    )

    with pytest.raises(KeyboardInterrupt):
        runtime.run(sleep=stop_after_two_cycles, log=lambda _message: None)

    assert calls == [
        ("register", "PC01"),
        ("process_pending",),
        ("active_policy",),
        ("deactivate",),
        ("heartbeat", "PC01"),
        ("process_pending",),
        ("active_policy",),
        ("deactivate",),
    ]


def test_client_factory_builds_with_fake_privileged_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Backend:
        def __init__(self, *_args):
            self.acknowledgements = []

        def pending_commands(self, _agent_id):
            return []

        def acknowledge_command(self, command_id, **values):
            self.acknowledgements.append((command_id, values))

        def active_policy(self, _agent_id):
            return None

        def report_policy_violation(self, *_args):
            return

        def register(self, _identity):
            return

        def heartbeat(self, _agent_id):
            return

    class Monitor:
        started = False
        deactivated = False

        def __init__(self, _report):
            return

        def start(self):
            self.started = True

        def activate(self, _session_id, _payload):
            return

        def deactivate(self):
            self.deactivated = True

    class Executor:
        maintained = False

        def execute(self, _request):
            raise AssertionError("no command should execute")

        def maintain(self):
            self.maintained = True

    monkeypatch.setattr(client_main, "AgentClient", Backend)
    monkeypatch.setattr(client_main, "BlockedDomainMonitor", Monitor)
    monkeypatch.setattr(
        client_main,
        "collect_identity",
        lambda *_args: WorkstationIdentity("PC01", "HOST", "192.0.2.1", "1.1.0"),
    )
    executor = Executor()

    runtime = client_main.build_client_runtime("PC01", executor, Backend())
    runtime.process_control_cycle()

    assert executor.maintained is False
    assert runtime._monitor.started is True
    assert runtime._monitor.deactivated is True


def test_client_and_service_entrypoint_checks_are_safe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client_main.main(["--check"])
    monkeypatch.setattr(
        service_main.AgentServiceRuntime,
        "build",
        lambda **_kwargs: SimpleNamespace(),
    )
    service_main.main(["--check"])

    output = capsys.readouterr().out
    assert "control_transport=gateway-ws privileged_transport=named-pipe" in output
    assert "component=agent-service status=config-valid transport=named-pipe" in output
