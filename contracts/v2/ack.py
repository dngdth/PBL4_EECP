from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from contracts.v2.common import OpaqueId, Sha256Hex, UtcDatetime, VersionedContract
from contracts.v2.errors import ErrorCode


class AckStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class Ack(VersionedContract):
    ack_id: OpaqueId
    command_id: OpaqueId
    status: AckStatus
    applied_hash: Sha256Hex | None = None
    service_version: OpaqueId
    error_code: ErrorCode | None = None
    error_message: str | None = None
    occurred_at: UtcDatetime
    correlation_id: OpaqueId

    @model_validator(mode="after")
    def validate_result(self) -> Ack:
        if self.status == AckStatus.SUCCEEDED and (
            self.error_code is not None or self.error_message is not None
        ):
            raise ValueError("successful ACK must not contain error details")
        if self.status != AckStatus.SUCCEEDED and self.error_code is None:
            raise ValueError("failed or rejected ACK requires error_code")
        return self
