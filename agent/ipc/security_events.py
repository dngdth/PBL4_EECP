from __future__ import annotations

import hmac
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent.service.security_event_journal import SecurityEventJournal


class SecurityEventIpcRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[2]
    operation: Literal["READ_SECURITY_EVENTS", "ACK_SECURITY_EVENTS"]
    request_id: str = Field(min_length=1, max_length=255)
    session_id: str = Field(min_length=1, max_length=255)
    token: str = Field(min_length=16, max_length=512)
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=100)
    event_ids: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("event_ids")
    @classmethod
    def validate_event_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("event_ids must be unique")
        if any(not value or len(value) > 255 for value in values):
            raise ValueError("event_id is invalid")
        return values


class SecurityEventIpcHandler:
    """Read/ack only local API; never dispatches privileged OS operations."""

    def __init__(self, journal: SecurityEventJournal, token: str):
        if len(token) < 16:
            raise ValueError("local security event IPC token must be at least 16 characters")
        self._journal = journal
        self._token = token

    def try_handle(self, payload: bytes) -> bytes | None:
        try:
            raw: Any = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or raw.get("operation") not in {
            "READ_SECURITY_EVENTS",
            "ACK_SECURITY_EVENTS",
        }:
            return None
        request_id = raw.get("request_id")
        safe_id = request_id if isinstance(request_id, str) else "invalid-request"
        try:
            request = SecurityEventIpcRequest.model_validate(raw)
        except ValidationError:
            return _response(safe_id, "REJECTED", error="invalid security event request")
        if not hmac.compare_digest(request.token, self._token):
            return _response(request.request_id, "REJECTED", error="unauthorized")
        if request.operation == "READ_SECURITY_EVENTS":
            values = self._journal.pending(
                session_id=request.session_id,
                after_sequence=request.after_sequence,
                limit=request.limit,
            )
            return _response(
                request.request_id,
                "SUCCEEDED",
                events=[{"sequence": sequence, **event.to_dict()} for sequence, event in values],
            )
        acknowledged = self._journal.acknowledge(
            request.session_id, request.event_ids
        )
        return _response(
            request.request_id, "SUCCEEDED", acknowledged_count=acknowledged
        )


def _response(request_id: str, status: str, **values: Any) -> bytes:
    return json.dumps(
        {"protocol_version": 2, "request_id": request_id, "status": status, **values},
        separators=(",", ":"),
    ).encode()
