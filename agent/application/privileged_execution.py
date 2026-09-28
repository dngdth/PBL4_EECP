from typing import Protocol

from contracts.v2 import ServiceRequest, ServiceResult


class PrivilegedExecutionPort(Protocol):
    """Application boundary for allowlisted privileged operations."""

    def execute(self, request: ServiceRequest) -> ServiceResult: ...
