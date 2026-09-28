from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.service.application.execution_service import ExecutionService
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
    compute_command_authorization,
    compute_policy_hash,
    compute_policy_signature,
)

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)
COMMAND_KEY = "phase-9-command-key-with-sufficient-entropy"
POLICY_KEY = "phase-9-policy-key-with-sufficient-entropy"


class RecordingExecutor:
    def __init__(self):
        self.requests: list[ServiceRequest] = []

    def execute(self, request: ServiceRequest) -> ServiceResult:
        self.requests.append(request)
        return ServiceResult(
            protocol_version=2,
            request_id=request.request_id,
            command_id=request.command_id,
            status=AckStatus.SUCCEEDED,
            applied_hash=request.policy_hash,
            service_version="2.0",
            occurred_at=NOW,
            correlation_id=request.correlation_id,
        )


def _request(
    operation: CommandType = CommandType.RESTORE_BASELINE,
    *,
    command_id: str = "CMD-001",
    session_id: str = "SES-A",
    correlation_id: str = "CORR-001",
    issued_at: datetime = NOW - timedelta(seconds=1),
    deadline: datetime = NOW + timedelta(minutes=5),
    sign_command: bool = True,
    sign_policy: bool = True,
) -> ServiceRequest:
    policy_hash = None
    if operation == CommandType.APPLY_POLICY:
        rules = PolicyRules()
        policy_hash = compute_policy_hash("LOCKDOWN", 1, rules)
        signature = (
            compute_policy_signature(POLICY_KEY, "LOCKDOWN", 1, rules, session_id)
            if sign_policy
            else None
        )
        payload = ApplyPolicyPayload(
            policy=PolicyEnvelope(
                protocol_version=2,
                policy_id="LOCKDOWN",
                policy_version=1,
                policy_hash=policy_hash,
                session_id=session_id,
                issued_at=issued_at,
                expires_at=deadline,
                rules=rules,
                signature=signature,
            )
        )
    elif operation == CommandType.HEALTH_CHECK:
        payload = HealthCheckPayload(nonce="health")
    else:
        payload = RestoreBaselinePayload()
    fields = {
        "protocol_version": 2,
        "command_id": command_id,
        "operation": operation,
        "session_id": session_id,
        "target_id": "AGT-001",
        "policy_hash": policy_hash,
        "issued_at": issued_at,
        "deadline": deadline,
        "correlation_id": correlation_id,
    }
    authorization = (
        compute_command_authorization(COMMAND_KEY, **fields)
        if sign_command and operation != CommandType.HEALTH_CHECK
        else None
    )
    return ServiceRequest(
        protocol_version=2,
        request_id=f"REQ-{command_id}",
        command_id=command_id,
        operation=operation,
        session_id=session_id,
        policy_hash=policy_hash,
        payload=payload,
        correlation_id=correlation_id,
        target_id="AGT-001",
        issued_at=issued_at,
        deadline=deadline,
        authorization=authorization,
    )


def _service(executor, replay_path: Path | None = None, **changes) -> ExecutionService:
    options = {
        "service_version": "2.0",
        "clock": lambda: NOW,
        "policy_verification_key": POLICY_KEY,
        "require_signed_policy": True,
        "command_verification_key": COMMAND_KEY,
        "require_authorized_commands": True,
        "command_target_id": "AGT-001",
        "replay_path": replay_path,
        "active_policy_hash": "a" * 64,
        "active_session_id": "SES-A",
    }
    options.update(changes)
    return ExecutionService(executor, **options)


@pytest.mark.parametrize("authorization", [None, "hmac-sha256:forged"])
def test_unsigned_or_forged_restore_is_rejected(authorization: str | None) -> None:
    executor = RecordingExecutor()
    request = _request().model_copy(update={"authorization": authorization})
    result = _service(executor).handle(request)
    assert result.status == AckStatus.REJECTED
    assert result.error_code == ErrorCode.INVALID_COMMAND
    assert executor.requests == []


def test_tampered_restore_and_wrong_session_are_rejected() -> None:
    executor = RecordingExecutor()
    tampered = _request().model_copy(update={"correlation_id": "CORR-TAMPERED"})
    assert _service(executor).handle(tampered).error_code == ErrorCode.INVALID_COMMAND

    wrong_session = _request(session_id="SES-B")
    result = _service(executor).handle(wrong_session)
    assert result.error_code == ErrorCode.SESSION_MISMATCH
    assert executor.requests == []


@pytest.mark.parametrize(
    "update",
    [
        {"session_id": "SES-TAMPERED"},
        {"target_id": "AGT-002"},
        {"operation": CommandType.APPLY_POLICY},
        {"deadline": NOW + timedelta(minutes=6)},
        {"correlation_id": "CORR-TAMPERED"},
        {"policy_hash": "b" * 64},
    ],
)
def test_each_authorized_restore_field_is_bound_against_tampering(update) -> None:
    executor = RecordingExecutor()
    result = _service(executor).handle(_request().model_copy(update=update))
    assert result.status == AckStatus.REJECTED
    assert result.error_code in {ErrorCode.INVALID_COMMAND, ErrorCode.TARGET_MISMATCH}
    assert executor.requests == []


def test_expired_privileged_command_is_rejected() -> None:
    executor = RecordingExecutor()
    result = _service(executor).handle(
        _request(
            issued_at=NOW - timedelta(minutes=2),
            deadline=NOW - timedelta(seconds=1),
        )
    )
    assert result.error_code == ErrorCode.COMMAND_EXPIRED
    assert executor.requests == []


def test_service_request_rejects_deadline_before_issue_time() -> None:
    with pytest.raises(ValueError, match="deadline"):
        _request().model_copy(
            update={
                "issued_at": NOW,
                "deadline": NOW - timedelta(seconds=1),
            }
        ).model_validate(
            {
                **_request().model_dump(),
                "issued_at": NOW,
                "deadline": NOW - timedelta(seconds=1),
            }
        )


def test_privileged_replay_survives_service_restart(tmp_path: Path) -> None:
    journal = tmp_path / "command-replay.json"
    request = _request()
    first_executor = RecordingExecutor()
    first = _service(first_executor, journal).handle(request)
    assert first.status == AckStatus.SUCCEEDED
    assert len(first_executor.requests) == 1

    restarted_executor = RecordingExecutor()
    replay = _service(
        restarted_executor,
        journal,
        active_policy_hash=None,
        active_session_id=None,
    ).handle(request)
    assert replay.status == AckStatus.SUCCEEDED
    assert restarted_executor.requests == []


def test_duplicate_and_changed_content_do_not_reexecute(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service = _service(executor, tmp_path / "replay.json")
    request = _request()
    assert service.handle(request).status == AckStatus.SUCCEEDED
    assert service.handle(request).status == AckStatus.SUCCEEDED

    changed = _request(correlation_id="CORR-CHANGED")
    conflict = service.handle(changed)
    assert conflict.error_code == ErrorCode.DUPLICATE_COMMAND
    assert len(executor.requests) == 1


def test_apply_requires_both_command_authorization_and_policy_signature() -> None:
    executor = RecordingExecutor()
    result = _service(executor).handle(
        _request(CommandType.APPLY_POLICY, sign_policy=False)
    )
    assert result.error_code == ErrorCode.INVALID_POLICY
    assert executor.requests == []


def test_unsigned_health_check_is_non_mutating() -> None:
    executor = RecordingExecutor()
    service = _service(executor)
    result = service.handle(_request(CommandType.HEALTH_CHECK, sign_command=False))
    assert result.status == AckStatus.SUCCEEDED
    assert result.applied_hash == "a" * 64
    assert service.active_session_id == "SES-A"
    assert service.active_policy_hash == "a" * 64


def test_corrupt_replay_journal_fails_closed(tmp_path: Path) -> None:
    journal = tmp_path / "corrupt.json"
    journal.write_text("{broken", encoding="utf-8")
    with pytest.raises(OSError, match="replay journal"):
        _service(RecordingExecutor(), journal)


def test_agent_client_has_no_command_signing_capability() -> None:
    root = Path(__file__).parents[4]
    client_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "agent" / "client").rglob("*.py")
    )
    assert "EECP_COMMAND_SIGNING_KEY" not in client_sources
    assert "compute_command_authorization" not in client_sources
