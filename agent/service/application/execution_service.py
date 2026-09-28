from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from agent.application.privileged_execution import PrivilegedExecutionPort
from agent.service.application.replay_journal import ReplayJournal
from contracts.v2 import (
    AckStatus,
    ApplyPolicyPayload,
    CommandType,
    ErrorCode,
    ServiceRequest,
    ServiceResult,
    verify_command_authorization,
    verify_policy_signature,
)


class ExecutionService(PrivilegedExecutionPort):
    """Validate, deduplicate, and dispatch allowlisted privileged requests."""

    def __init__(
        self,
        executor: PrivilegedExecutionPort,
        *,
        service_version: str = "service",
        replay_capacity: int = 1024,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        policy_verification_key: str = "",
        require_signed_policy: bool = False,
        command_verification_key: str = "",
        require_authorized_commands: bool = False,
        command_target_id: str = "",
        replay_path: Path | None = None,
        active_policy_hash: str | None = None,
        active_session_id: str | None = None,
    ):
        if replay_capacity < 1:
            raise ValueError("replay_capacity must be positive")
        if require_signed_policy and not policy_verification_key:
            raise ValueError("policy verification key is required for signed-policy mode")
        if require_authorized_commands and not command_verification_key:
            raise ValueError(
                "command verification key is required for authorized-command mode"
            )
        if require_authorized_commands and not command_target_id:
            raise ValueError("Agent identity is required for authorized-command mode")
        self._executor = executor
        self._service_version = service_version
        self._replay_capacity = replay_capacity
        self._clock = clock
        self._policy_verification_key = policy_verification_key
        self._require_signed_policy = require_signed_policy
        self._command_verification_key = command_verification_key
        self._require_authorized_commands = require_authorized_commands
        self._command_target_id = command_target_id
        self._processed: OrderedDict[str, tuple[str, ServiceResult]] = OrderedDict()
        self._journal = (
            ReplayJournal(replay_path, replay_capacity, clock=clock)
            if replay_path is not None
            else None
        )
        self._active_policy_hash = active_policy_hash
        self._active_session_id = active_session_id

    def handle(self, request: ServiceRequest) -> ServiceResult:
        validation_failure = self._validate_authorization(request)
        if validation_failure is not None:
            return validation_failure

        fingerprint = hashlib.sha256(request.to_json().encode("utf-8")).hexdigest()
        previous = self._processed.get(request.command_id)
        if previous is None and self._journal is not None:
            previous = self._journal.get(request.command_id)
        if previous is not None:
            previous_fingerprint, result = previous
            if request.command_id in self._processed:
                self._processed.move_to_end(request.command_id)
            if previous_fingerprint == fingerprint:
                return result
            return self._failure(
                request,
                ErrorCode.DUPLICATE_COMMAND,
                "command_id was already processed with different content",
                rejected=True,
            )

        validation_failure = self._validate(request)
        if validation_failure is not None:
            return validation_failure

        result = self._executor.execute(request)
        if result.status == AckStatus.SUCCEEDED:
            if request.operation == CommandType.APPLY_POLICY:
                self._active_policy_hash = result.applied_hash
                self._active_session_id = request.session_id
            elif request.operation == CommandType.RESTORE_BASELINE:
                self._active_policy_hash = None
                self._active_session_id = None
            elif (
                request.operation == CommandType.HEALTH_CHECK
                and self._active_policy_hash is not None
            ):
                result = result.model_copy(update={"applied_hash": self._active_policy_hash})

        self._processed[request.command_id] = (fingerprint, result)
        self._processed.move_to_end(request.command_id)
        while len(self._processed) > self._replay_capacity:
            self._processed.popitem(last=False)
        if (
            self._journal is not None
            and request.operation in {CommandType.APPLY_POLICY, CommandType.RESTORE_BASELINE}
            and request.deadline is not None
        ):
            self._journal.put(
                request.command_id,
                fingerprint,
                request.deadline,
                result,
            )
        return result

    def execute(self, request: ServiceRequest) -> ServiceResult:
        return self.handle(request)

    @property
    def active_policy_hash(self) -> str | None:
        return self._active_policy_hash

    @property
    def active_session_id(self) -> str | None:
        return self._active_session_id

    def _validate(self, request: ServiceRequest) -> ServiceResult | None:
        if request.operation not in {
            CommandType.APPLY_POLICY,
            CommandType.RESTORE_BASELINE,
            CommandType.HEALTH_CHECK,
        }:
            return self._failure(
                request,
                ErrorCode.UNSUPPORTED_OPERATION,
                "operation is not allowlisted",
                rejected=True,
            )
        if request.operation == CommandType.APPLY_POLICY:
            if not isinstance(request.payload, ApplyPolicyPayload):
                return self._failure(
                    request,
                    ErrorCode.INVALID_POLICY,
                    "APPLY_POLICY payload is invalid",
                    rejected=True,
                )
            expires_at = request.payload.policy.expires_at
            if expires_at is not None and expires_at <= self._clock():
                return self._failure(
                    request,
                    ErrorCode.POLICY_EXPIRED,
                    "policy has expired",
                    rejected=True,
                )
            policy = request.payload.policy
            if self._require_signed_policy and not verify_policy_signature(
                policy, self._policy_verification_key
            ):
                return self._failure(
                    request,
                    ErrorCode.INVALID_POLICY,
                    "policy signature verification failed",
                    rejected=True,
                )
        if (
            self._require_authorized_commands
            and request.operation == CommandType.RESTORE_BASELINE
            and request.session_id != self._active_session_id
        ):
            return self._failure(
                request,
                ErrorCode.SESSION_MISMATCH,
                "restore session does not match the active policy session",
                rejected=True,
            )
        return None

    def _validate_authorization(self, request: ServiceRequest) -> ServiceResult | None:
        if request.operation not in {CommandType.APPLY_POLICY, CommandType.RESTORE_BASELINE}:
            return None
        if not self._require_authorized_commands:
            return None
        if (
            request.target_id is None
            or request.issued_at is None
            or request.deadline is None
            or request.authorization is None
        ):
            return self._failure(
                request,
                ErrorCode.INVALID_COMMAND,
                "privileged command authorization fields are required",
                rejected=True,
            )
        if request.target_id != self._command_target_id:
            return self._failure(
                request,
                ErrorCode.TARGET_MISMATCH,
                "command target does not match this Agent",
                rejected=True,
            )
        if request.deadline < request.issued_at or request.deadline < self._clock():
            return self._failure(
                request,
                ErrorCode.COMMAND_EXPIRED,
                "command deadline has passed",
                rejected=True,
            )
        fields = {
            "protocol_version": request.protocol_version,
            "command_id": request.command_id,
            "operation": request.operation,
            "session_id": request.session_id,
            "target_id": request.target_id,
            "policy_hash": request.policy_hash,
            "issued_at": request.issued_at,
            "deadline": request.deadline,
            "correlation_id": request.correlation_id,
        }
        if not verify_command_authorization(
            request.authorization,
            self._command_verification_key,
            **fields,
        ):
            return self._failure(
                request,
                ErrorCode.INVALID_COMMAND,
                "privileged command authorization verification failed",
                rejected=True,
            )
        return None

    def _failure(
        self,
        request: ServiceRequest,
        error_code: ErrorCode,
        message: str,
        *,
        rejected: bool,
    ) -> ServiceResult:
        return ServiceResult(
            protocol_version=2,
            request_id=request.request_id,
            command_id=request.command_id,
            status=AckStatus.REJECTED if rejected else AckStatus.FAILED,
            service_version=self._service_version,
            error_code=error_code,
            error_message=message,
            occurred_at=self._clock(),
            correlation_id=request.correlation_id,
        )
