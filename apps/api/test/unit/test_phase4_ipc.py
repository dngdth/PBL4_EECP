import json
import struct
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from agent.client.infrastructure.named_pipe_executor import NamedPipePrivilegedExecutor
from agent.ipc.framing import (
    FrameError,
    FrameTooLarge,
    decode_frame,
    encode_frame,
    read_frame,
    write_frame,
)
from agent.ipc.named_pipe_client import IpcTransportError
from agent.ipc.protocol import (
    ServiceProtocolHandler,
    deserialize_request,
    deserialize_result,
    serialize_request,
)
from agent.ipc.security import PIPE_ACL_SDDL, validate_pipe_acl_sddl
from agent.service.application.execution_service import ExecutionService
from agent.service.runtime import AgentServiceRuntime, ServiceLifecycle
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
MAX_SIZE = 1024


def _request(
    operation: CommandType = CommandType.HEALTH_CHECK,
    *,
    request_id: str = "req-1",
    command_id: str = "cmd-1",
    expires_at: datetime | None = None,
) -> ServiceRequest:
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
                issued_at=NOW - timedelta(minutes=1),
                expires_at=expires_at,
                rules=rules,
            )
        )
    elif operation == CommandType.RESTORE_BASELINE:
        payload = RestoreBaselinePayload()
    else:
        payload = HealthCheckPayload(nonce="nonce-1")
    return ServiceRequest(
        protocol_version=2,
        request_id=request_id,
        command_id=command_id,
        operation=operation,
        session_id="ses-1",
        policy_hash=policy_hash,
        payload=payload,
        correlation_id="corr-1",
    )


def _success(request: ServiceRequest, applied_hash: str | None = None) -> ServiceResult:
    return ServiceResult(
        protocol_version=2,
        request_id=request.request_id,
        command_id=request.command_id,
        status=AckStatus.SUCCEEDED,
        applied_hash=applied_hash,
        service_version="1.1.0",
        occurred_at=NOW,
        correlation_id=request.correlation_id,
    )


def test_length_prefix_framing_handles_partial_reads_and_writes() -> None:
    payload = b'{"hello":"world"}'
    encoded = encode_frame(payload, MAX_SIZE)
    source = bytearray(encoded)

    def partial_read(size: int) -> bytes:
        amount = min(size, 2)
        value = bytes(source[:amount])
        del source[:amount]
        return value

    assert read_frame(partial_read, MAX_SIZE) == payload

    output = bytearray()

    def partial_write(value: bytes) -> int:
        amount = min(3, len(value))
        output.extend(value[:amount])
        return amount

    write_frame(partial_write, payload, MAX_SIZE)
    assert decode_frame(bytes(output), MAX_SIZE) == payload


@pytest.mark.parametrize(
    "frame",
    [b"", b"\x00\x00", struct.pack("!I", 0), struct.pack("!I", 4) + b"ab"],
)
def test_framing_rejects_invalid_or_truncated_frames(frame: bytes) -> None:
    with pytest.raises(FrameError):
        decode_frame(frame, MAX_SIZE)


def test_framing_rejects_oversized_messages_without_buffering() -> None:
    with pytest.raises(FrameTooLarge):
        encode_frame(b"x" * (MAX_SIZE + 1), MAX_SIZE)
    with pytest.raises(FrameTooLarge):
        decode_frame(struct.pack("!I", MAX_SIZE + 1), MAX_SIZE)


@pytest.mark.parametrize("operation", list(CommandType))
def test_protocol_accepts_only_valid_allowlisted_requests(operation: CommandType) -> None:
    request = _request(operation)
    handler = ServiceProtocolHandler(lambda value: _success(value), "1.1.0", lambda: NOW)

    result = deserialize_result(handler(serialize_request(request)))

    assert result.status == AckStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda value: b"{not-json", ErrorCode.INVALID_MESSAGE),
        (
            lambda value: json.dumps({**json.loads(value), "protocol_version": 99}).encode(),
            ErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
        ),
        (
            lambda value: json.dumps(
                {key: item for key, item in json.loads(value).items() if key != "command_id"}
            ).encode(),
            ErrorCode.INVALID_COMMAND,
        ),
        (
            lambda value: json.dumps({**json.loads(value), "operation": "RUN_SHELL"}).encode(),
            ErrorCode.UNSUPPORTED_OPERATION,
        ),
        (
            lambda value: json.dumps({**json.loads(value), "unexpected": True}).encode(),
            ErrorCode.INVALID_MESSAGE,
        ),
    ],
)
def test_protocol_rejects_untrusted_messages(mutate, expected: ErrorCode) -> None:
    payload = serialize_request(_request())
    handler = ServiceProtocolHandler(pytest.fail, "1.1.0", lambda: NOW)

    result = deserialize_result(handler(mutate(payload)))

    assert result.status == AckStatus.REJECTED
    assert result.error_code == expected


def test_protocol_rejects_policy_hash_mismatch() -> None:
    value = json.loads(serialize_request(_request(CommandType.APPLY_POLICY)))
    value["policy_hash"] = "0" * 64
    handler = ServiceProtocolHandler(pytest.fail, "1.1.0", lambda: NOW)

    result = deserialize_result(handler(json.dumps(value).encode()))

    assert result.error_code == ErrorCode.POLICY_HASH_MISMATCH


class RecordingExecutor:
    def __init__(self):
        self.requests: list[ServiceRequest] = []

    def execute(self, request: ServiceRequest) -> ServiceResult:
        self.requests.append(request)
        return _success(request, request.policy_hash)


def test_execution_service_replay_returns_cached_result_without_reexecution() -> None:
    executor = RecordingExecutor()
    service = ExecutionService(executor, service_version="1.1.0", clock=lambda: NOW)
    request = _request(CommandType.APPLY_POLICY)

    first = service.handle(request)
    replay = service.handle(request)

    assert replay is first
    assert executor.requests == [request]


def test_execution_service_rejects_conflicting_duplicate_command() -> None:
    executor = RecordingExecutor()
    service = ExecutionService(executor, service_version="1.1.0", clock=lambda: NOW)
    service.handle(_request(command_id="cmd-shared", request_id="req-1"))

    result = service.handle(_request(command_id="cmd-shared", request_id="req-2"))

    assert result.error_code == ErrorCode.DUPLICATE_COMMAND
    assert len(executor.requests) == 1


def test_execution_service_rejects_expired_policy() -> None:
    executor = RecordingExecutor()
    service = ExecutionService(executor, service_version="1.1.0", clock=lambda: NOW)

    result = service.handle(
        _request(CommandType.APPLY_POLICY, expires_at=NOW - timedelta(seconds=1))
    )

    assert result.error_code == ErrorCode.POLICY_EXPIRED
    assert executor.requests == []


def test_health_check_reports_active_policy_hash_without_sensitive_data() -> None:
    executor = RecordingExecutor()
    service = ExecutionService(executor, service_version="1.1.0", clock=lambda: NOW)
    applied = _request(CommandType.APPLY_POLICY)
    service.handle(applied)

    result = service.handle(_request(command_id="health-1", request_id="health-req"))

    assert result.status == AckStatus.SUCCEEDED
    assert result.applied_hash == applied.policy_hash
    assert result.service_version == "1.1.0"


def test_named_pipe_executor_round_trip_and_structured_disconnect() -> None:
    request = _request()

    class LoopbackTransport:
        def request(self, payload: bytes) -> bytes:
            parsed = deserialize_request(payload)
            return _success(parsed).to_json().encode()

    result = NamedPipePrivilegedExecutor(
        LoopbackTransport(), "1.1.0", clock=lambda: NOW
    ).execute(request)
    assert result.status == AckStatus.SUCCEEDED

    class DisconnectedTransport:
        def request(self, _payload: bytes) -> bytes:
            raise IpcTransportError("service stopped")

    failure = NamedPipePrivilegedExecutor(
        DisconnectedTransport(), "1.1.0", clock=lambda: NOW
    ).execute(request)
    assert failure.status == AckStatus.FAILED
    assert failure.error_code == ErrorCode.EXECUTION_FAILED


def test_client_transport_can_succeed_after_service_restart() -> None:
    request = _request()

    class RestartingTransport:
        attempts = 0

        def request(self, payload: bytes) -> bytes:
            self.attempts += 1
            if self.attempts == 1:
                raise IpcTransportError("service unavailable")
            parsed = deserialize_request(payload)
            return _success(parsed).to_json().encode()

    transport = RestartingTransport()
    executor = NamedPipePrivilegedExecutor(transport, "1.1.0", clock=lambda: NOW)

    assert executor.execute(request).status == AckStatus.FAILED
    assert executor.execute(request).status == AckStatus.SUCCEEDED


def test_pipe_acl_is_explicit_and_excludes_world_and_anonymous() -> None:
    validate_pipe_acl_sddl()
    upper = PIPE_ACL_SDDL.upper()
    assert ";;;SY)" in upper
    assert ";;;BA)" in upper
    assert ";;;AU)" in upper
    assert ";;;WD)" not in upper
    assert ";;;AN)" not in upper


def test_service_lifecycle_owns_maintenance_after_client_disappears(tmp_path) -> None:
    class Enforcer:
        maintained = 0

        def __init__(self, _path):
            return

        def apply(self, _payload):
            return "a" * 64

        def restore(self):
            return

        def maintain(self):
            self.maintained += 1

    class Server:
        started = False
        stopped = False

        def start(self, handler):
            self.started = callable(handler)

        def stop(self):
            self.stopped = True

    enforcer = Enforcer(tmp_path / "state.json")
    server = Server()
    runtime = AgentServiceRuntime.build(
        policy_mode="audit",
        state_path=tmp_path / "state.json",
        service_version="1.1.0",
        server=server,
        maintenance_interval_seconds=0.01,
        audit_factory=lambda _path: enforcer,
    )

    runtime.start(lambda payload: payload)
    deadline = time.monotonic() + 1.0
    while enforcer.maintained < 2 and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    runtime.stop()

    assert server.started is True
    assert server.stopped is True
    assert enforcer.maintained >= 2
    assert runtime.lifecycle == ServiceLifecycle.STOPPED
