from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from agent.application.privileged_execution import PrivilegedExecutionPort
from contracts.v2 import (
    AckStatus,
    ApplyPolicyPayload,
    CommandType,
    ErrorCode,
    ServiceRequest,
    ServiceResult,
)


class PolicyEnforcer(Protocol):
    def apply(self, payload: dict[str, Any]) -> str: ...

    def restore(self) -> None: ...

    def maintain(self) -> None: ...


class InProcessPrivilegedExecutor(PrivilegedExecutionPort):
    """Same-process adapter around the existing policy enforcer."""

    def __init__(
        self,
        enforcer: PolicyEnforcer,
        service_version: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._enforcer = enforcer
        self._service_version = service_version
        self._clock = clock

    def execute(self, request: ServiceRequest) -> ServiceResult:
        try:
            if request.operation == CommandType.APPLY_POLICY:
                payload = request.payload
                if not isinstance(payload, ApplyPolicyPayload):
                    return self._failure(
                        request,
                        ErrorCode.INVALID_COMMAND,
                        "APPLY_POLICY payload is invalid",
                        rejected=True,
                    )
                applied_hash = self._enforcer.apply(_legacy_policy_payload(payload))
                if applied_hash != request.policy_hash:
                    return self._failure(
                        request,
                        ErrorCode.POLICY_HASH_MISMATCH,
                        "enforcer returned an unexpected policy hash",
                    )
                return self._success(request, applied_hash)

            if request.operation == CommandType.RESTORE_BASELINE:
                self._enforcer.restore()
                return self._success(request)

            if request.operation == CommandType.HEALTH_CHECK:
                return self._success(request)

            return self._failure(
                request,
                ErrorCode.UNSUPPORTED_OPERATION,
                f"unsupported operation: {request.operation}",
                rejected=True,
            )
        except ValueError as exc:
            code = (
                ErrorCode.INVALID_POLICY
                if request.operation == CommandType.APPLY_POLICY
                else ErrorCode.EXECUTION_FAILED
            )
            return self._failure(request, code, str(exc))
        except OSError as exc:
            return self._failure(request, ErrorCode.EXECUTION_FAILED, str(exc))

    def maintain(self) -> None:
        self._enforcer.maintain()

    def _success(
        self,
        request: ServiceRequest,
        applied_hash: str | None = None,
    ) -> ServiceResult:
        return ServiceResult(
            protocol_version=2,
            request_id=request.request_id,
            command_id=request.command_id,
            status=AckStatus.SUCCEEDED,
            applied_hash=applied_hash,
            service_version=self._service_version,
            occurred_at=self._clock(),
            correlation_id=request.correlation_id,
        )

    def _failure(
        self,
        request: ServiceRequest,
        error_code: ErrorCode,
        error_message: str,
        *,
        rejected: bool = False,
    ) -> ServiceResult:
        return ServiceResult(
            protocol_version=2,
            request_id=request.request_id,
            command_id=request.command_id,
            status=AckStatus.REJECTED if rejected else AckStatus.FAILED,
            service_version=self._service_version,
            error_code=error_code,
            error_message=error_message,
            occurred_at=self._clock(),
            correlation_id=request.correlation_id,
        )


def _legacy_policy_payload(payload: ApplyPolicyPayload) -> dict[str, Any]:
    policy = payload.policy
    return {
        "format": "eecp-policy/v1",
        "policy_hash": policy.policy_hash,
        "version": policy.policy_version,
        "profile": policy.policy_id,
        "rules": policy.rules.model_dump(mode="json", exclude_none=True),
    }
