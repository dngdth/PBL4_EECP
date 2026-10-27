from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from contracts.v2 import EventType


class RequestTransport(Protocol):
    def request(self, payload: bytes) -> bytes: ...


class SecurityEventSensor:
    """Moves Service events into AgentEventSpool before acknowledging the journal."""

    def __init__(
        self,
        transport: RequestTransport,
        token: str,
        report: Callable[..., None],
        *,
        batch_size: int = 50,
    ):
        if len(token) < 16:
            raise ValueError("local security event IPC token must be at least 16 characters")
        self._transport = transport
        self._token = token
        self._report = report
        self._batch_size = max(1, min(batch_size, 100))
        self._session_id: str | None = None

    def activate(self, session_id: str, _payload: dict[str, Any]) -> None:
        self._session_id = session_id

    def deactivate(self) -> None:
        self._session_id = None

    def poll(self) -> None:
        if self._session_id is None:
            return
        response = self._request(
            "READ_SECURITY_EVENTS",
            session_id=self._session_id,
            limit=self._batch_size,
        )
        if response.get("status") != "SUCCEEDED":
            raise OSError("Service security event read was rejected")
        acknowledged: list[str] = []
        events = response.get("events", [])
        if not isinstance(events, list):
            raise ValueError("Service returned an invalid security event batch")
        for raw in events:
            event = _validate_event(raw, self._session_id)
            self._report(
                self._session_id,
                EventType(event["event_type"]),
                {
                    "severity": event["severity"],
                    "category": event["category"],
                    "action": event["action"],
                    "source": event["source"],
                    **event["details"],
                },
                event_id=event["event_id"],
                occurred_at=datetime.fromisoformat(event["occurred_at"]),
                correlation_id=event["correlation_id"],
            )
            acknowledged.append(event["event_id"])
        if acknowledged:
            ack = self._request(
                "ACK_SECURITY_EVENTS",
                session_id=self._session_id,
                event_ids=acknowledged,
            )
            if ack.get("status") != "SUCCEEDED":
                raise OSError("Service security event acknowledgement failed")

    def _request(self, operation: str, **values: Any) -> dict[str, Any]:
        request = {
            "protocol_version": 2,
            "operation": operation,
            "request_id": f"local_{uuid.uuid4().hex}",
            "token": self._token,
            "after_sequence": 0,
            "limit": self._batch_size,
            "event_ids": [],
            **values,
        }
        response = self._transport.request(
            json.dumps(request, separators=(",", ":")).encode()
        )
        value = json.loads(response)
        if not isinstance(value, dict) or value.get("request_id") != request["request_id"]:
            raise ValueError("Service security event response does not match request")
        return value


def _validate_event(raw: object, session_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("invalid Service security event")
    required = {
        "event_id",
        "session_id",
        "event_type",
        "category",
        "action",
        "severity",
        "source",
        "occurred_at",
        "correlation_id",
        "details",
    }
    if not required.issubset(raw) or raw["session_id"] != session_id:
        raise ValueError("Service security event session mismatch")
    if raw["severity"] not in {"INFO", "WARNING", "CRITICAL"}:
        raise ValueError("invalid Service security event severity")
    if not isinstance(raw["details"], dict):
        raise ValueError("invalid Service security event details")
    return raw
