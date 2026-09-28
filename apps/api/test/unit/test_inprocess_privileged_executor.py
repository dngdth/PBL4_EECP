from datetime import UTC, datetime

import pytest

from agent.infrastructure.inprocess_executor import InProcessPrivilegedExecutor
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
    compute_policy_hash,
)

NOW = datetime(2026, 9, 28, 10, 20, 31, tzinfo=UTC)
RULES = {
    "applications": {"allow": ["vscode.exe"], "deny": ["anydesk.exe"]},
    "network": {"block": ["generative_ai"]},
    "devices": {"usb": "deny"},
}
HASH = compute_policy_hash("INTERNET_NO_AI", 1, PolicyRules.model_validate(RULES))


def _request(operation: CommandType) -> ServiceRequest:
    if operation == CommandType.APPLY_POLICY:
        policy = PolicyEnvelope.from_legacy(
            policy_id="INTERNET_NO_AI",
            policy_version=1,
            policy_hash=HASH,
            session_id="ses-1",
            issued_at=NOW,
            rules=RULES,
        )
        payload = ApplyPolicyPayload(policy=policy)
        policy_hash = HASH
    elif operation == CommandType.RESTORE_BASELINE:
        payload = RestoreBaselinePayload(baseline="NORMAL")
        policy_hash = HASH
    else:
        payload = HealthCheckPayload(nonce="health-1")
        policy_hash = None
    return ServiceRequest(
        protocol_version=2,
        request_id=f"req-{operation.value}",
        command_id=f"cmd-{operation.value}",
        operation=operation,
        session_id="ses-1",
        policy_hash=policy_hash,
        payload=payload,
        correlation_id="corr-1",
    )


class RecordingEnforcer:
    def __init__(self, *, apply_error: Exception | None = None):
        self.calls = []
        self.apply_error = apply_error
        self.returned_hash = HASH

    def apply(self, payload):
        self.calls.append(("apply", payload))
        if self.apply_error is not None:
            raise self.apply_error
        return self.returned_hash

    def restore(self):
        self.calls.append(("restore",))

    def maintain(self):
        self.calls.append(("maintain",))


def _executor(enforcer: RecordingEnforcer) -> InProcessPrivilegedExecutor:
    return InProcessPrivilegedExecutor(
        enforcer,
        service_version="1.1.0",
        clock=lambda: NOW,
    )


def test_apply_policy_delegates_legacy_payload_and_returns_result() -> None:
    enforcer = RecordingEnforcer()

    result = _executor(enforcer).execute(_request(CommandType.APPLY_POLICY))

    assert result.status == AckStatus.SUCCEEDED
    assert result.applied_hash == HASH
    assert enforcer.calls == [
        (
            "apply",
            {
                "format": "eecp-policy/v1",
                "policy_hash": HASH,
                "version": 1,
                "profile": "INTERNET_NO_AI",
                "rules": RULES,
            },
        )
    ]


def test_restore_baseline_delegates_to_existing_enforcer() -> None:
    enforcer = RecordingEnforcer()

    result = _executor(enforcer).execute(_request(CommandType.RESTORE_BASELINE))

    assert result.status == AckStatus.SUCCEEDED
    assert result.applied_hash is None
    assert enforcer.calls == [("restore",)]


def test_health_check_succeeds_without_touching_enforcer() -> None:
    enforcer = RecordingEnforcer()

    result = _executor(enforcer).execute(_request(CommandType.HEALTH_CHECK))

    assert result.status == AckStatus.SUCCEEDED
    assert enforcer.calls == []


def test_maintain_delegates_to_existing_enforcer() -> None:
    enforcer = RecordingEnforcer()

    _executor(enforcer).maintain()

    assert enforcer.calls == [("maintain",)]


def test_unknown_operation_is_rejected_without_execution() -> None:
    enforcer = RecordingEnforcer()
    malformed = _request(CommandType.HEALTH_CHECK).model_copy(
        update={"operation": "RUN_SHELL"}
    )

    result = _executor(enforcer).execute(malformed)

    assert result.status == AckStatus.REJECTED
    assert result.error_code == ErrorCode.UNSUPPORTED_OPERATION
    assert enforcer.calls == []


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (ValueError("invalid policy"), ErrorCode.INVALID_POLICY),
        (OSError("access denied"), ErrorCode.EXECUTION_FAILED),
    ],
)
def test_executor_maps_known_enforcer_errors(
    error: Exception,
    expected_code: ErrorCode,
) -> None:
    result = _executor(RecordingEnforcer(apply_error=error)).execute(
        _request(CommandType.APPLY_POLICY)
    )

    assert result.status == AckStatus.FAILED
    assert result.error_code == expected_code
    assert result.error_message == str(error)


def test_executor_rejects_unexpected_applied_hash() -> None:
    enforcer = RecordingEnforcer()
    enforcer.returned_hash = "0" * 64

    result = _executor(enforcer).execute(_request(CommandType.APPLY_POLICY))

    assert result.status == AckStatus.FAILED
    assert result.error_code == ErrorCode.POLICY_HASH_MISMATCH
