from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects.enums import AgentStatus, IncidentStatus, Severity


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class RegisterAgentRequest(ApiModel):
    id: str = Field(min_length=1)
    hostname: str = Field(min_length=1)
    ip_address: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)


class LatestIncidentView(ApiModel):
    id: str
    session_id: str
    category: str
    severity: Severity
    status: IncidentStatus
    created_at: datetime


class AgentView(ApiModel):
    id: str
    hostname: str
    ip_address: str
    status: AgentStatus
    agent_version: str
    last_seen: datetime
    created_at: datetime
    presence_health: str | None = None
    service_health: str | None = None
    active_policy_hash: str | None = None
    gateway_id: str | None = None
    latest_incident: LatestIncidentView | None = None
