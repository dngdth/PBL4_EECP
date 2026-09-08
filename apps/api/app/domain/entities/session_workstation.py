from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.exceptions.errors import PolicyValidationError
from app.domain.value_objects.primitives import new_id


@dataclass(frozen=True, slots=True)
class SessionWorkstation:
    id: str
    session_id: str
    agent_id: str
    assigned_at: datetime

    @classmethod
    def assign(
        cls, session_id: str, agent_id: str, at: datetime
    ) -> SessionWorkstation:
        normalized_session = session_id.strip()
        normalized_agent = agent_id.strip()
        if not normalized_session or not normalized_agent:
            raise PolicyValidationError("session id and agent id are required")
        return cls(new_id("sws"), normalized_session, normalized_agent, at)
