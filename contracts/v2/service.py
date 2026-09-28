from __future__ import annotations

from pydantic import model_validator

from contracts.v2.ack import AckStatus
from contracts.v2.command import (
    ApplyPolicyPayload,
    CommandAuthorization,
    CommandPayload,
    CommandType,
    HealthCheckPayload,
    RestoreBaselinePayload,
)
from contracts.v2.common import OpaqueId, Sha256Hex, UtcDatetime, VersionedContract
from contracts.v2.errors import ErrorCode


class ServiceRequest(VersionedContract):
    request_id: OpaqueId
    command_id: OpaqueId
    operation: CommandType
    session_id: OpaqueId
    policy_hash: Sha256Hex | None = None
    payload: CommandPayload
    correlation_id: OpaqueId
    target_id: OpaqueId | None = None
    issued_at: UtcDatetime | None = None
    deadline: UtcDatetime | None = None
    authorization: CommandAuthorization | None = None

    @model_validator(mode="after")
    def validate_operation_payload(self) -> ServiceRequest:
        expected_payload = {
            CommandType.APPLY_POLICY: ApplyPolicyPayload,
            CommandType.RESTORE_BASELINE: RestoreBaselinePayload,
            CommandType.HEALTH_CHECK: HealthCheckPayload,
        }[self.operation]
        if not isinstance(self.payload, expected_payload):
            raise ValueError(f"payload does not match {self.operation}")
        if self.operation == CommandType.APPLY_POLICY:
            if self.policy_hash is None or self.policy_hash != self.payload.policy.policy_hash:
                raise ValueError("APPLY_POLICY requires the embedded policy hash")
            if self.session_id != self.payload.policy.session_id:
                raise ValueError("service request and policy session_id must match")
        elif self.operation == CommandType.HEALTH_CHECK and self.policy_hash is not None:
            raise ValueError("HEALTH_CHECK must not carry policy_hash")
        return self


class ServiceResult(VersionedContract):
    request_id: OpaqueId
    command_id: OpaqueId
    status: AckStatus
    applied_hash: Sha256Hex | None = None
    service_version: OpaqueId
    error_code: ErrorCode | None = None
    error_message: str | None = None
    occurred_at: UtcDatetime
    correlation_id: OpaqueId

    @model_validator(mode="after")
    def validate_result(self) -> ServiceResult:
        if self.status == AckStatus.SUCCEEDED and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError("successful result must not contain error details")
        if self.status != AckStatus.SUCCEEDED and self.error_code is None:
            raise ValueError("failed or rejected result requires error_code")
        return self
