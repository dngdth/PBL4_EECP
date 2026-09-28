from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime

from agent.application.privileged_execution import PrivilegedExecutionPort
from contracts.v2 import (
    AckStatus,
    ApplyPolicyPayload,
    CommandType,
    ErrorCode,
    ServiceRequest,
    ServiceResult,
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
    ):
        if replay_capacity < 1:
            raise ValueError("replay_capacity must be positive")
        self._executor = executor
        self._service_version = service_version
        self._replay_capacity = replay_capacity
        self._clock = clock
        self._processed: OrderedDict[str, tuple[str, ServiceResult]] = OrderedDict()
        self._active_policy_hash: str | None = None
        self._active_session_id: str | None = None

    def handle(self, request: ServiceRequest) -> ServiceResult:
        validation_failure = self._validate(request)
        if validation_failure is not None:
            return validation_failure

        fingerprint = hashlib.sha256(request.to_json().encode("utf-8")).hexdigest()
        previous = self._processed.get(request.command_id)
        if previous is not None:
            previous_fingerprint, result = previous
            self._processed.move_to_end(request.command_id)
            if previous_fingerprint == fingerprint:
                return result
            return self._failure(
                request,
                ErrorCode.DUPLICATE_COMMAND,
                "command_id was already processed with different content",
                rejected=True,
            )

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
