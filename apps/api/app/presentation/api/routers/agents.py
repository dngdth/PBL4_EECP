from fastapi import APIRouter, Request, status

from app.application.dtos.agents import RegisterAgentInput
from app.presentation.api.deps import (
    HeartbeatAgentUseCase,
    ListAgentsUseCase,
    RegisterAgentUseCase,
)
from app.presentation.schemas.agents import AgentView, RegisterAgentRequest

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.post(
    "/register",
    response_model=AgentView,
    status_code=status.HTTP_201_CREATED,
)
def register_agent(
    body: RegisterAgentRequest,
    use_case: RegisterAgentUseCase,
) -> AgentView:
    agent = use_case(
        RegisterAgentInput(
            agent_id=body.id,
            hostname=body.hostname,
            ip_address=body.ip_address,
            agent_version=body.agent_version,
        )
    )
    return AgentView.model_validate(agent)


@router.post("/{agent_id}/heartbeat", response_model=AgentView)
def heartbeat_agent(
    agent_id: str,
    use_case: HeartbeatAgentUseCase,
) -> AgentView:
    return AgentView.model_validate(use_case(agent_id))


@router.get("", response_model=list[AgentView])
def list_agents(use_case: ListAgentsUseCase, request: Request) -> list[AgentView]:
    values = []
    latest_by_agent = {}
    with request.app.state.container.database.unit_of_work() as uow:
        for session in uow.sessions.list_all():
            for incident in uow.incidents.list_for_session(session.id):
                agent_id = incident.workstation_id
                current = latest_by_agent.get(agent_id)
                if agent_id and (current is None or incident.created_at > current.created_at):
                    latest_by_agent[agent_id] = incident
    for agent in use_case():
        presence = request.app.state.presence.get_agent(agent.id) or {}
        latest = latest_by_agent.get(agent.id)
        values.append(
            AgentView.model_validate(
                {
                    "id": agent.id,
                    "hostname": agent.hostname,
                    "ip_address": agent.ip_address,
                    "status": agent.status,
                    "agent_version": agent.agent_version,
                    "last_seen": agent.last_seen,
                    "created_at": agent.created_at,
                    "presence_health": presence.get("health"),
                    "service_health": presence.get("service_health"),
                    "active_policy_hash": presence.get("active_policy_hash"),
                    "gateway_id": presence.get("gateway_id"),
                    "latest_incident": latest,
                }
            )
        )
    return values
