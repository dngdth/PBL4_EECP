from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from agent.application.privileged_execution import PrivilegedExecutionPort
from agent.ipc.named_pipe_client import IpcTransportError, RequestTransport
from agent.ipc.protocol import deserialize_result, serialize_request
from contracts.v2 import AckStatus, ErrorCode, ServiceRequest, ServiceResult


class NamedPipePrivilegedExecutor(PrivilegedExecutionPort):
    """Non-privileged client adapter for the privileged Named Pipe service."""

    def __init__(
        self,
        transport: RequestTransport,
        client_version: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._transport = transport
        self._client_version = client_version
        self._clock = clock

    def execute(self, request: ServiceRequest) -> ServiceResult:
        try:
            payload = self._transport.request(serialize_request(request))
            result = deserialize_result(payload)
            if result.request_id != request.request_id or result.command_id != request.command_id:
                raise ValueError("Service response does not match the request")
            return result
        except (IpcTransportError, OSError, ValueError) as exc:
            return ServiceResult(
                protocol_version=2,
                request_id=request.request_id,
                command_id=request.command_id,
                status=AckStatus.FAILED,
                service_version=self._client_version,
                error_code=ErrorCode.EXECUTION_FAILED,
                error_message=f"privileged service unavailable: {str(exc)[:400]}",
                occurred_at=self._clock(),
                correlation_id=request.correlation_id,
            )
