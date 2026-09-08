from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects.enums import (
    AgentStatus,
    SessionState,
    Severity,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateManagementSessionRequest(ApiModel):
    name: str = Field(min_length=1)
    room: str = Field(min_length=1)
    agent_ids: list[str] = Field(min_length=1)
    actor: str = Field(default="teacher", min_length=1)
    policy_profile: str = Field(default="INTERNET_NO_AI", min_length=1)


class CreatePipelineSessionRequest(ApiModel):
    exam_name: str = Field(min_length=1)
    room_id: str = Field(min_length=1)
    gateway_id: str = Field(min_length=1)
    workstation_ids: list[str] = Field(min_length=1)
    actor: str = Field(default="teacher", min_length=1)


class UpdateSessionStatusRequest(ApiModel):
    status: SessionState
    actor: str = Field(default="teacher", min_length=1)


class DeployPolicyRequest(ApiModel):
    profile: str = Field(min_length=1)
    rules: dict[str, Any]
    actor: str = Field(default="teacher", min_length=1)


class PreflightCheckRequest(ApiModel):
    name: str = Field(min_length=1)
    passed: bool
    critical: bool = True
    details: str | None = None


class SubmitPreflightRequest(ApiModel):
    checks: list[PreflightCheckRequest] = Field(min_length=1)
    actor: str = Field(default="agent", min_length=1)


class StartSessionRequest(ApiModel):
    actor: str = Field(default="teacher", min_length=1)
    force: bool = False
    reason: str | None = None


class FinishSessionRequest(ApiModel):
    actor: str = Field(default="teacher", min_length=1)


class TelemetryRequest(ApiModel):
    workstation_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    severity: Severity = Severity.INFO
    category: str = Field(min_length=1)
    action: str = Field(min_length=1)
    destination: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TelemetryAcceptedView(ApiModel):
    event_id: str
    incident_id: str | None


class SessionView(ApiModel):
    id: str
    exam_name: str
    room_id: str
    gateway_id: str | None
    state: str
    policy: dict[str, Any] | None
    gateway_policy_hash: str | None
    gateway_restored: bool
    force_started: bool
    force_start_reason: str | None
    workstations: dict[str, dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    aggregate_version: int


class AssignedAgentView(ApiModel):
    id: str
    hostname: str | None
    ip_address: str | None
    status: AgentStatus | None
    last_seen: datetime | None
    assigned_at: datetime | None
    policy_status: str


class PolicyViolationView(ApiModel):
    workstation_id: str
    destination: str | None
    category: str
    occurred_at: datetime


class SessionDetailView(SessionView):
    name: str
    room: str
    status: SessionState
    agent_count: int
    agents: list[AssignedAgentView]
    violations: list[PolicyViolationView]


class ErrorView(ApiModel):
    code: str
    detail: str
