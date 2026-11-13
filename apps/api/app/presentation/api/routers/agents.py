from fastapi import APIRouter, HTTPException, Request, status

from app.application.dtos.agents import RegisterAgentInput
from app.application.security import Permission
from app.presentation.api.deps import (
    HeartbeatAgentUseCase,
    ListAgentsUseCase,
    RegisterAgentUseCase,
    actor_name,
    authorize,
    scoped_resources,
)
from app.presentation.schemas.agents import AgentView, RegisterAgentRequest

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _guard_agent_http_mutation(request: Request, agent_id: str) -> None:
    if request.app.state.settings.environment == "production-like":
        request.app.state.container.security_audit.record(
            action="AUTHORIZATION_DENIED",
            actor=actor_name(request, agent_id),
            actor_type="AGENT",
            resource_type="AGENT_HTTP",
            resource_id=agent_id,
            reason_code="DIRECT_AGENT_HTTP_DISABLED",
            correlation_id=request.headers.get("x-correlation-id"),
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "direct HTTP agent registration and heartbeat are disabled in production-like "
            "environment; use Gateway WebSocket",
        )
    authorize(request, Permission.MANAGE_USERS)


@router.post(
    "/register",
    response_model=AgentView,
    status_code=status.HTTP_201_CREATED,
)
def register_agent(
    body: RegisterAgentRequest,
    use_case: RegisterAgentUseCase,
    request: Request,
) -> AgentView:
    _guard_agent_http_mutation(request, body.id)
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
    request: Request,
) -> AgentView:
    _guard_agent_http_mutation(request, agent_id)
    return AgentView.model_validate(use_case(agent_id))


@router.get("", response_model=list[AgentView])
def list_agents(use_case: ListAgentsUseCase, request: Request) -> list[AgentView]:
    authorize(request, Permission.VIEW_AGENT)
    scope = scoped_resources(request)
    values = []
    with request.app.state.container.database.unit_of_work() as uow:
        latest_by_agent = uow.incidents.latest_by_workstation()
    for agent in use_case():
        if scope is not None and agent.id not in scope[0]:
            continue
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
