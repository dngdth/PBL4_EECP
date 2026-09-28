from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from contracts.v2.common import ContractModel, OpaqueId, Sha256Hex, UtcDatetime, VersionedContract
from contracts.v2.policy import PolicyEnvelope


class CommandType(StrEnum):
    APPLY_POLICY = "APPLY_POLICY"
    RESTORE_BASELINE = "RESTORE_BASELINE"
    HEALTH_CHECK = "HEALTH_CHECK"


class ApplyPolicyPayload(ContractModel):
    policy: PolicyEnvelope


class RestoreBaselinePayload(ContractModel):
    baseline: str = "NORMAL"

    @model_validator(mode="after")
    def validate_baseline(self) -> RestoreBaselinePayload:
        if self.baseline != "NORMAL":
            raise ValueError("the only supported baseline is NORMAL")
        return self


class HealthCheckPayload(ContractModel):
    nonce: OpaqueId | None = None


CommandPayload = ApplyPolicyPayload | RestoreBaselinePayload | HealthCheckPayload


class Command(VersionedContract):
    command_id: OpaqueId
    command_type: CommandType
    target_id: OpaqueId
    session_id: OpaqueId
    issued_at: UtcDatetime
    deadline: UtcDatetime
    policy_hash: Sha256Hex | None = None
    payload: CommandPayload
    correlation_id: OpaqueId

    @model_validator(mode="after")
    def validate_command(self) -> Command:
        if self.deadline < self.issued_at:
            raise ValueError("deadline must not be earlier than issued_at")
        expected_payload = {
            CommandType.APPLY_POLICY: ApplyPolicyPayload,
            CommandType.RESTORE_BASELINE: RestoreBaselinePayload,
            CommandType.HEALTH_CHECK: HealthCheckPayload,
        }[self.command_type]
        if not isinstance(self.payload, expected_payload):
            raise ValueError(f"payload does not match {self.command_type}")
        if self.command_type == CommandType.APPLY_POLICY:
            if self.policy_hash is None:
                raise ValueError("APPLY_POLICY requires policy_hash")
            if self.policy_hash != self.payload.policy.policy_hash:
                raise ValueError("command policy_hash does not match payload policy_hash")
            if self.session_id != self.payload.policy.session_id:
                raise ValueError("command and policy session_id must match")
        elif self.command_type == CommandType.HEALTH_CHECK and self.policy_hash is not None:
            raise ValueError("HEALTH_CHECK must not carry policy_hash")
        return self
