from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from contracts.v2 import AckStatus, ErrorCode, ServiceRequest, ServiceResult


def serialize_request(request: ServiceRequest) -> bytes:
    return request.to_json().encode("utf-8")


def deserialize_request(payload: bytes) -> ServiceRequest:
    return ServiceRequest.model_validate_json(payload)


def serialize_result(result: ServiceResult) -> bytes:
    return result.to_json().encode("utf-8")


def deserialize_result(payload: bytes) -> ServiceResult:
    return ServiceResult.model_validate_json(payload)


class ServiceProtocolHandler:
    """Turn untrusted JSON bytes into validated Protocol v2 service calls."""

    def __init__(
        self,
        dispatch: Callable[[ServiceRequest], ServiceResult],
        service_version: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._dispatch = dispatch
        self._service_version = service_version
        self._clock = clock

    def __call__(self, payload: bytes) -> bytes:
        raw = _best_effort_object(payload)
        try:
            request = deserialize_request(payload)
        except ValidationError as exc:
            return serialize_result(self._invalid_result(raw, _validation_error_code(exc)))
        except (UnicodeDecodeError, ValueError):
            return serialize_result(self._invalid_result(raw, ErrorCode.INVALID_MESSAGE))
        try:
            return serialize_result(self._dispatch(request))
        except Exception:
            return serialize_result(
                self._failure_for_request(
                    request,
                    ErrorCode.INTERNAL_ERROR,
                    "unexpected service failure",
                )
            )

    def _invalid_result(
        self, raw: dict[str, Any], error_code: ErrorCode
    ) -> ServiceResult:
        return ServiceResult(
            protocol_version=2,
            request_id=_safe_id(raw.get("request_id"), "invalid-request"),
            command_id=_safe_id(raw.get("command_id"), "invalid-command"),
            status=AckStatus.REJECTED,
            service_version=self._service_version,
            error_code=error_code,
            error_message="request failed Protocol v2 validation",
            occurred_at=self._clock(),
            correlation_id=_safe_id(raw.get("correlation_id"), "invalid-correlation"),
        )

    def _failure_for_request(
        self, request: ServiceRequest, error_code: ErrorCode, message: str
    ) -> ServiceResult:
        return ServiceResult(
            protocol_version=2,
            request_id=request.request_id,
            command_id=request.command_id,
            status=AckStatus.FAILED,
            service_version=self._service_version,
            error_code=error_code,
            error_message=message,
            occurred_at=self._clock(),
            correlation_id=request.correlation_id,
        )


def _best_effort_object(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_id(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and 0 < len(value.strip()) <= 255 else fallback


def _validation_error_code(exc: ValidationError) -> ErrorCode:
    errors = exc.errors()
    if any(tuple(error.get("loc", ())) == ("protocol_version",) for error in errors):
        return ErrorCode.UNSUPPORTED_PROTOCOL_VERSION
    if any(tuple(error.get("loc", ())) == ("operation",) for error in errors):
        return ErrorCode.UNSUPPORTED_OPERATION
    messages = " ".join(str(error.get("msg", "")) for error in errors).lower()
    if (
        "policy_hash" in messages
        or "policy hash" in messages
        or "canonical policy content" in messages
    ):
        return ErrorCode.POLICY_HASH_MISMATCH
    if any(tuple(error.get("loc", ())) == ("command_id",) for error in errors):
        return ErrorCode.INVALID_COMMAND
    return ErrorCode.INVALID_MESSAGE
