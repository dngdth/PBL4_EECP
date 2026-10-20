from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.application.policy_commands import PolicyCommandProcessor
from agent.client.infrastructure.gateway_control_client import _legacy_command
from agent.service.application.execution_service import ExecutionService
from contracts.v2 import (
    AckStatus,
    ApplyPolicyPayload,
    Command,
    CommandType,
    ErrorCode,
    PolicyEnvelope,
    PolicyRules,
    RestoreBaselinePayload,
    ServiceRequest,
    ServiceResult,
    compute_command_authorization,
    compute_policy_hash,
    compute_policy_signature,
)

NOW = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
POLICY_KEY = "test-policy-key-32-bytes-long!!"
COMMAND_KEY = "test-cmd-key-32-bytes-long!!!!"
AGENT_ID = "PC01"
SESSION_ID = "SES-EXAM-01"


class MockExecutor:
    def __init__(self, fail_first_n: int = 0, error_message: str = "resource busy"):
        self.requests: list[ServiceRequest] = []
        self.fail_first_n = fail_first_n
        self.call_count = 0
        self.error_message = error_message

    def execute(self, request: ServiceRequest) -> ServiceResult:
        self.requests.append(request)
        self.call_count += 1
        if self.call_count <= self.fail_first_n:
            return ServiceResult(
                protocol_version=2,
                request_id=request.request_id,
                command_id=request.command_id,
                status=AckStatus.FAILED,
                service_version="2.0",
                error_code=ErrorCode.EXECUTION_FAILED,
                error_message=self.error_message,
                occurred_at=datetime.now(UTC),
                correlation_id=request.correlation_id,
            )
        return ServiceResult(
            protocol_version=2,
            request_id=request.request_id,
            command_id=request.command_id,
            status=AckStatus.SUCCEEDED,
            applied_hash=request.policy_hash,
            service_version="2.0",
            occurred_at=datetime.now(UTC),
            correlation_id=request.correlation_id,
        )


class MockCommandClient:
    def __init__(self, commands: list[dict[str, Any]] | None = None):
        self._commands = commands or []
        self.acks: list[dict[str, Any]] = []

    def pending_commands(self, agent_id: str) -> list[dict[str, Any]]:
        return list(self._commands)

    def acknowledge_command(
        self,
        command_id: str,
        *,
        success: bool,
        policy_hash: str | None = None,
        error: str | None = None,
        actor: str,
        service_version: str | None = None,
    ) -> None:
        self.acks.append(
            {
                "command_id": command_id,
                "success": success,
                "policy_hash": policy_hash,
                "error": error,
                "actor": actor,
                "service_version": service_version,
            }
        )


def _make_policy_envelope(
    *,
    issued_at: datetime = NOW,
    expires_at: datetime | None = None,
    rules: dict[str, Any] | None = None,
    session_id: str = SESSION_ID,
) -> PolicyEnvelope:
    rules_obj = PolicyRules.model_validate(rules or {})
    phash = compute_policy_hash("standard", 1, rules_obj)
    sig = compute_policy_signature(POLICY_KEY, "standard", 1, rules_obj, session_id)
    return PolicyEnvelope.from_legacy(
        policy_id="standard",
        policy_version=1,
        policy_hash=phash,
        session_id=session_id,
        issued_at=issued_at,
        expires_at=expires_at or (issued_at + timedelta(minutes=10)),
        rules=rules or {},
        signature=sig,
    )


def _make_contract_command(
    *,
    command_id: str = "cmd-001",
    command_type: CommandType = CommandType.APPLY_POLICY,
    issued_at: datetime = NOW,
    deadline: datetime | None = None,
    session_id: str = SESSION_ID,
    policy_envelope: PolicyEnvelope | None = None,
) -> Command:
    deadline_val = deadline or (issued_at + timedelta(minutes=5))
    if command_type == CommandType.APPLY_POLICY:
        envelope = policy_envelope or _make_policy_envelope(
            issued_at=issued_at, session_id=session_id
        )
        payload = ApplyPolicyPayload(policy=envelope)
        policy_hash = envelope.policy_hash
    elif command_type == CommandType.RESTORE_BASELINE:
        payload = RestoreBaselinePayload(baseline="NORMAL")
        policy_hash = None
    else:
        raise ValueError("unsupported")

    auth = compute_command_authorization(
        COMMAND_KEY,
        protocol_version=2,
        command_id=command_id,
        operation=command_type,
        session_id=session_id,
        target_id=AGENT_ID,
        policy_hash=policy_hash,
        issued_at=issued_at,
        deadline=deadline_val,
        correlation_id=f"corr_{command_id}",
    )
    return Command(
        protocol_version=2,
        command_id=command_id,
        command_type=command_type,
        target_id=AGENT_ID,
        session_id=session_id,
        issued_at=issued_at,
        deadline=deadline_val,
        policy_hash=policy_hash,
        payload=payload,
        correlation_id=f"corr_{command_id}",
        authorization=auth,
    )


def _make_service(
    executor: MockExecutor,
    *,
    clock: datetime | Callable[[], datetime] = NOW,
    replay_path: Path | None = None,
    active_session_id: str | None = SESSION_ID,
    active_policy_hash: str | None = None,
) -> ExecutionService:
    clock_fn = clock if callable(clock) else (lambda: clock)
    return ExecutionService(
        executor,
        service_version="2.0",
        clock=clock_fn,
        policy_verification_key=POLICY_KEY,
        require_signed_policy=True,
        command_verification_key=COMMAND_KEY,
        require_authorized_commands=True,
        command_target_id=AGENT_ID,
        replay_path=replay_path,
        active_session_id=active_session_id,
        active_policy_hash=active_policy_hash,
    )


# ---------------------------------------------------------------------------
# TEST 1 – Transient failure
# ---------------------------------------------------------------------------
def test_test1_transient_failure_and_retry_semantics() -> None:
    """TEST 1: Transient failure caches FAILED; retry requires new command_id."""
    executor = MockExecutor(fail_first_n=1, error_message="firewall mutex busy")
    service = _make_service(executor)

    cmd1 = _make_contract_command(command_id="cmd-fail-1")
    legacy1 = _legacy_command(cmd1)

    # First attempt: transient failure
    client1 = MockCommandClient([legacy1])
    processor1 = PolicyCommandProcessor(client1, AGENT_ID, service, clock=lambda: NOW)
    processor1.process_pending()

    assert len(client1.acks) == 1
    assert client1.acks[0]["success"] is False
    assert "firewall mutex busy" in client1.acks[0]["error"]
    assert executor.call_count == 1

    # Re-sending same command_id returns cached FAILED result without re-executing
    client_replay = MockCommandClient([legacy1])
    processor_replay = PolicyCommandProcessor(
        client_replay, AGENT_ID, service, clock=lambda: NOW + timedelta(seconds=2)
    )
    processor_replay.process_pending()

    assert len(client_replay.acks) == 1
    assert client_replay.acks[0]["success"] is False
    assert executor.call_count == 1  # No second execution of same command_id!

    # Retry according to EECP V2 contract: Backend creates a NEW command with NEW command_id
    cmd2 = _make_contract_command(command_id="cmd-retry-2")
    legacy2 = _legacy_command(cmd2)
    client2 = MockCommandClient([legacy2])
    processor2 = PolicyCommandProcessor(
        client2, AGENT_ID, service, clock=lambda: NOW + timedelta(seconds=10)
    )
    processor2.process_pending()

    assert len(client2.acks) == 1
    assert client2.acks[0]["success"] is True
    assert executor.call_count == 2  # New command executes and succeeds!


# ---------------------------------------------------------------------------
# TEST 2 – Duplicate after success
# ---------------------------------------------------------------------------
def test_test2_duplicate_after_success_returns_cached_result() -> None:
    """TEST 2: APPLY_POLICY succeeds; lost ACK re-dispatch returns cached result."""
    executor = MockExecutor()
    service = _make_service(executor)

    cmd = _make_contract_command(command_id="cmd-success-1")
    legacy = _legacy_command(cmd)

    client1 = MockCommandClient([legacy])
    processor1 = PolicyCommandProcessor(client1, AGENT_ID, service, clock=lambda: NOW)
    processor1.process_pending()

    assert len(client1.acks) == 1
    assert client1.acks[0]["success"] is True
    assert executor.call_count == 1

    # Gateway re-sends identical command later because ACK was lost in network
    client2 = MockCommandClient([legacy])
    processor2 = PolicyCommandProcessor(
        client2, AGENT_ID, service, clock=lambda: NOW + timedelta(seconds=5)
    )
    processor2.process_pending()

    assert len(client2.acks) == 1
    assert client2.acks[0]["success"] is True
    # Crucial: Must NOT re-execute policy side effects!
    assert executor.call_count == 1


# ---------------------------------------------------------------------------
# TEST 3 – Different payload, same command ID
# ---------------------------------------------------------------------------
def test_test3_different_payload_same_command_id_rejected() -> None:
    """TEST 3: Same command_id with different payload is rejected as DUPLICATE_COMMAND."""
    executor = MockExecutor()
    service = _make_service(executor)

    cmd1 = _make_contract_command(command_id="cmd-conflict-1")
    legacy1 = _legacy_command(cmd1)

    client1 = MockCommandClient([legacy1])
    processor1 = PolicyCommandProcessor(client1, AGENT_ID, service, clock=lambda: NOW)
    processor1.process_pending()
    assert client1.acks[0]["success"] is True

    # Same command_id, but different policy rules (different content)
    different_policy = _make_policy_envelope(
        rules={"network": {"blocked_domains": ["gambling.invalid"]}}
    )
    cmd2 = _make_contract_command(
        command_id="cmd-conflict-1",
        policy_envelope=different_policy,
    )
    legacy2 = _legacy_command(cmd2)

    client2 = MockCommandClient([legacy2])
    processor2 = PolicyCommandProcessor(
        client2, AGENT_ID, service, clock=lambda: NOW + timedelta(seconds=1)
    )
    processor2.process_pending()

    assert len(client2.acks) == 1
    assert client2.acks[0]["success"] is False
    assert (
        "DUPLICATE_COMMAND" in client2.acks[0]["error"]
        or "already processed with different content" in client2.acks[0]["error"]
    )
    assert executor.call_count == 1  # Did not execute conflicting command!


# ---------------------------------------------------------------------------
# TEST 4 – FAILED result after Service restart
# ---------------------------------------------------------------------------
def test_test4_failed_result_persisted_in_journal_across_restart(tmp_path: Path) -> None:
    """TEST 4: FAILED command in ReplayJournal survives restart, prevents unsafe retry."""
    journal_path = tmp_path / "replay.json"
    executor1 = MockExecutor(fail_first_n=1, error_message="low-level driver failed")
    service1 = _make_service(executor1, replay_path=journal_path)

    cmd = _make_contract_command(command_id="cmd-fail-journal-1")
    legacy = _legacy_command(cmd)

    client1 = MockCommandClient([legacy])
    processor1 = PolicyCommandProcessor(client1, AGENT_ID, service1, clock=lambda: NOW)
    processor1.process_pending()
    assert client1.acks[0]["success"] is False
    assert executor1.call_count == 1

    # Restart Service: new instance reading same replay journal on disk
    executor2 = MockExecutor()
    service2 = _make_service(executor2, replay_path=journal_path)

    client2 = MockCommandClient([legacy])
    processor2 = PolicyCommandProcessor(
        client2, AGENT_ID, service2, clock=lambda: NOW + timedelta(seconds=3)
    )
    processor2.process_pending()

    assert len(client2.acks) == 1
    assert client2.acks[0]["success"] is False
    # Replay journal prevented silent re-execution of failed command after restart
    assert executor2.call_count == 0


# ---------------------------------------------------------------------------
# TEST 5 – Partial execution failure
# ---------------------------------------------------------------------------
def test_test5_partial_execution_failure_not_reexecuted() -> None:
    """TEST 5: Partial execution failure is cached; no automatic side-effect re-execution."""
    executor = MockExecutor(fail_first_n=1, error_message="partial apply: hosts file locked")
    service = _make_service(executor)

    cmd = _make_contract_command(command_id="cmd-partial-1")
    legacy = _legacy_command(cmd)

    client1 = MockCommandClient([legacy])
    processor1 = PolicyCommandProcessor(client1, AGENT_ID, service, clock=lambda: NOW)
    processor1.process_pending()
    assert client1.acks[0]["success"] is False

    # Second delivery
    client2 = MockCommandClient([legacy])
    processor2 = PolicyCommandProcessor(
        client2, AGENT_ID, service, clock=lambda: NOW + timedelta(seconds=2)
    )
    processor2.process_pending()

    assert client2.acks[0]["success"] is False
    assert executor.call_count == 1  # Never re-executes non-idempotent failed operation


# ---------------------------------------------------------------------------
# TEST 6 – Authorization and deadline
# ---------------------------------------------------------------------------
def test_test6_authorization_deadline_and_session_rejection() -> None:
    """TEST 6: Replay cache must NOT bypass target, signature, session, or deadline."""
    executor = MockExecutor()
    current_time = [NOW]
    service = _make_service(executor, clock=lambda: current_time[0])

    cmd = _make_contract_command(command_id="cmd-sec-1")
    legacy = _legacy_command(cmd)

    client1 = MockCommandClient([legacy])
    processor1 = PolicyCommandProcessor(client1, AGENT_ID, service, clock=lambda: current_time[0])
    processor1.process_pending()
    assert client1.acks[0]["success"] is True

    # 1. Target mismatch
    bad_target = dict(legacy)
    bad_target["target_id"] = "WRONG_PC"
    client_target = MockCommandClient([bad_target])
    PolicyCommandProcessor(
        client_target, AGENT_ID, service, clock=lambda: current_time[0]
    ).process_pending()
    assert client_target.acks[0]["success"] is False
    assert (
        "TARGET_MISMATCH" in client_target.acks[0]["error"]
        or "does not match this Agent" in client_target.acks[0]["error"]
    )

    # 2. Forged signature
    bad_sig = dict(legacy)
    bad_sig["authorization"] = "hmac-sha256:00000000000000000000000000000000"
    client_sig = MockCommandClient([bad_sig])
    PolicyCommandProcessor(
        client_sig, AGENT_ID, service, clock=lambda: current_time[0]
    ).process_pending()
    assert client_sig.acks[0]["success"] is False
    assert (
        "INVALID_COMMAND" in client_sig.acks[0]["error"]
        or "verification failed" in client_sig.acks[0]["error"]
    )

    # 3. Expired deadline
    current_time[0] = NOW + timedelta(minutes=10)
    client_exp = MockCommandClient([legacy])
    PolicyCommandProcessor(
        client_exp, AGENT_ID, service, clock=lambda: current_time[0]
    ).process_pending()
    assert client_exp.acks[0]["success"] is False
    assert (
        "COMMAND_EXPIRED" in client_exp.acks[0]["error"]
        or "deadline has passed" in client_exp.acks[0]["error"]
    )

    # 4. RESTORE_BASELINE with wrong active session
    current_time[0] = NOW
    restore_cmd = _make_contract_command(
        command_id="cmd-sec-restore",
        command_type=CommandType.RESTORE_BASELINE,
        session_id="SES-EXAM-01",
    )
    restore_legacy = _legacy_command(restore_cmd)

    # Active session is SES-EXAM-02 (different from command's SES-EXAM-01)
    service_diff_session = _make_service(
        executor, active_session_id="SES-EXAM-02", clock=lambda: current_time[0]
    )
    client_restore = MockCommandClient([restore_legacy])
    PolicyCommandProcessor(
        client_restore, AGENT_ID, service_diff_session, clock=lambda: current_time[0]
    ).process_pending()
    assert client_restore.acks[0]["success"] is False
    assert (
        "SESSION_MISMATCH" in client_restore.acks[0]["error"]
        or "does not match the active policy session" in client_restore.acks[0]["error"]
    )


# ---------------------------------------------------------------------------
# TEST 7 – Stable fingerprint
# ---------------------------------------------------------------------------
def test_test7_stable_fingerprint_across_retransmissions() -> None:
    """TEST 7: ServiceRequest fingerprint must be stable when Gateway retransmits."""
    cmd = _make_contract_command(command_id="cmd-stable-1")
    legacy = _legacy_command(cmd)

    processor1 = PolicyCommandProcessor(
        MockCommandClient(), AGENT_ID, MockExecutor(), clock=lambda: NOW
    )
    req1 = processor1._build_service_request(
        legacy["id"],
        legacy["session_id"],
        legacy["type"],
        legacy["payload"],
        correlation_id=legacy.get("correlation_id"),
        target_id=legacy.get("target_id"),
        issued_at=legacy.get("issued_at"),
        deadline=legacy.get("deadline"),
        authorization=legacy.get("authorization"),
    )

    # Reconstructed 5 seconds later
    processor2 = PolicyCommandProcessor(
        MockCommandClient(), AGENT_ID, MockExecutor(), clock=lambda: NOW + timedelta(seconds=5)
    )
    req2 = processor2._build_service_request(
        legacy["id"],
        legacy["session_id"],
        legacy["type"],
        legacy["payload"],
        correlation_id=legacy.get("correlation_id"),
        target_id=legacy.get("target_id"),
        issued_at=legacy.get("issued_at"),
        deadline=legacy.get("deadline"),
        authorization=legacy.get("authorization"),
    )

    import hashlib
    fp1 = hashlib.sha256(req1.to_json().encode("utf-8")).hexdigest()
    fp2 = hashlib.sha256(req2.to_json().encode("utf-8")).hexdigest()

    assert fp1 == fp2, (
        f"Fingerprint must be stable across retransmissions!\n"
        f"req1 issued_at: {req1.payload.policy.issued_at}\n"
        f"req2 issued_at: {req2.payload.policy.issued_at}"
    )


# ---------------------------------------------------------------------------
# TEST 8 – New command after failure
# ---------------------------------------------------------------------------
def test_test8_new_command_after_failure_processes_normally() -> None:
    """TEST 8: Backend issues a new command with new command_id after a failure."""
    executor = MockExecutor(fail_first_n=1, error_message="transient IPC drop")
    service = _make_service(executor)

    # Command 1 fails
    cmd1 = _make_contract_command(command_id="cmd-fail-8")
    legacy1 = _legacy_command(cmd1)
    client1 = MockCommandClient([legacy1])
    PolicyCommandProcessor(client1, AGENT_ID, service, clock=lambda: NOW).process_pending()
    assert client1.acks[0]["success"] is False

    # Command 2 (new ID) is generated by Backend
    cmd2 = _make_contract_command(command_id="cmd-new-8")
    legacy2 = _legacy_command(cmd2)
    client2 = MockCommandClient([legacy2])
    PolicyCommandProcessor(
        client2, AGENT_ID, service, clock=lambda: NOW + timedelta(seconds=15)
    ).process_pending()

    assert client2.acks[0]["success"] is True
    assert executor.call_count == 2
