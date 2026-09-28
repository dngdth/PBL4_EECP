from __future__ import annotations

import hashlib
import hmac
import json
from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints, model_validator

from contracts.v2.common import ContractModel, OpaqueId, Sha256Hex, UtcDatetime, VersionedContract
from contracts.v2.policy import PolicyEnvelope

COMMAND_AUTHORIZATION_DOMAIN = "EECP-COMMAND-V2"
CommandAuthorization = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256, strict=True),
]


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
    authorization: CommandAuthorization | None = None

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


def canonical_command_authorization(
    *,
    protocol_version: int,
    command_id: str,
    operation: CommandType | str,
    session_id: str,
    target_id: str,
    policy_hash: str | None,
    issued_at: UtcDatetime,
    deadline: UtcDatetime,
    correlation_id: str,
) -> str:
    """Canonical state-changing command fields; excludes transport/runtime data."""

    payload = {
        "command_id": command_id,
        "correlation_id": correlation_id,
        "deadline": deadline.isoformat().replace("+00:00", "Z"),
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "operation": operation.value if isinstance(operation, CommandType) else operation,
        "policy_hash": policy_hash,
        "protocol_version": protocol_version,
        "session_id": session_id,
        "target_id": target_id,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compute_command_authorization(signing_key: str, **fields: object) -> str:
    if not signing_key:
        raise ValueError("command signing key must not be empty")
    canonical = canonical_command_authorization(**fields)  # type: ignore[arg-type]
    message = f"{COMMAND_AUTHORIZATION_DOMAIN}\n{canonical}".encode()
    digest = hmac.new(signing_key.encode(), message, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def verify_command_authorization(
    authorization: str | None,
    verification_key: str,
    **fields: object,
) -> bool:
    if not authorization or not verification_key:
        return False
    expected = compute_command_authorization(verification_key, **fields)
    return hmac.compare_digest(authorization, expected)
