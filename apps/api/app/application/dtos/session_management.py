from dataclasses import dataclass
from datetime import datetime

from app.domain.entities.exam_session import ExamSession
from app.domain.value_objects.enums import AgentStatus, SessionState


@dataclass(frozen=True, slots=True)
class CreateExamSessionInput:
    name: str
    room: str
    agent_ids: list[str]
    actor: str = "teacher"
    policy_profile: str = "INTERNET_NO_AI"


@dataclass(frozen=True, slots=True)
class UpdateExamSessionStatusInput:
    session_id: str
    status: SessionState
    actor: str = "teacher"


@dataclass(frozen=True, slots=True)
class AssignedAgentDetails:
    id: str
    hostname: str | None
    ip_address: str | None
    status: AgentStatus | None
    last_seen: datetime | None
    assigned_at: datetime | None
    policy_status: str


@dataclass(frozen=True, slots=True)
class PolicyViolationDetails:
    workstation_id: str
    destination: str | None
    category: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ExamSessionDetails:
    session: ExamSession
    agents: list[AssignedAgentDetails]
    violations: list[PolicyViolationDetails]

    @property
    def agent_count(self) -> int:
        return len(self.session.workstations)
