from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request, status

from app.application.dtos.exam_pipeline import (
    CreateSessionInput,
    DeployPolicyInput,
    RequestPreflightInput,
    StartSessionInput,
    SubmitPreflightInput,
    TelemetryInput,
)
from app.application.dtos.session_management import (
    CreateExamSessionInput,
    ExamSessionDetails,
    UpdateExamSessionStatusInput,
)
from app.application.security import Permission
from app.domain.entities.exam_session import ExamSession, PreflightCheck
from app.domain.entities.operations import TelemetryEvent
from app.presentation.api.deps import (
    CreateExamSessionUseCase,
    GetExamSessionUseCase,
    ListExamSessionsUseCase,
    Service,
    UpdateExamSessionStatusUseCase,
    authorize,
)
from app.presentation.schemas.exam_pipeline import (
    AssignedAgentView,
    CreateManagementSessionRequest,
    CreatePipelineSessionRequest,
    DeployPolicyRequest,
    FinishSessionRequest,
    PolicyViolationView,
    PreflightHealthCheckView,
    RequestPreflightRequest,
    SecurityEventPage,
    SecurityEventView,
    SessionDetailView,
    SessionView,
    StartSessionRequest,
    SubmitPreflightRequest,
    TelemetryAcceptedView,
    TelemetryRequest,
    UpdateSessionStatusRequest,
)

router = APIRouter(prefix="/api/v1", tags=["exam-pipeline"])


@router.get(
    "/sessions/{session_id}/security-events",
    response_model=SecurityEventPage,
)
def list_security_events(
    session_id: str,
    service: Service,
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    agent_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = Query(default=None, pattern="^(INFO|WARNING|CRITICAL)$"),
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> SecurityEventPage:
    authorize(request, Permission.VIEW_SESSION, session_id=session_id)
    events, total = service.list_security_events(
        session_id,
        offset=offset,
        limit=limit,
        agent_id=agent_id,
        event_type=event_type,
        severity=severity,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return SecurityEventPage(
        items=[_security_event_view(event) for event in events],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/sessions",
    response_model=SessionDetailView,
    status_code=status.HTTP_201_CREATED,
)
def create_session(
    body: CreateManagementSessionRequest | CreatePipelineSessionRequest,
    service: Service,
    create_management: CreateExamSessionUseCase,
    get_management: GetExamSessionUseCase,
    request: Request,
) -> SessionDetailView:
    authorize(request, Permission.CONTROL_AGENT)
    if isinstance(body, CreatePipelineSessionRequest):
        session = service.create_session(CreateSessionInput(**body.model_dump()))
    else:
        details = create_management(CreateExamSessionInput(**body.model_dump()))
        session = details.session
    return _session_detail_view(get_management(session.id), request)


@router.get("/sessions", response_model=list[SessionDetailView])
def list_sessions(
    use_case: ListExamSessionsUseCase, request: Request
) -> list[SessionDetailView]:
    authorize(request, Permission.VIEW_SESSION)
    details = use_case()
    principal = getattr(request.state, "principal", None)
    if principal is not None and principal.role.value != "ADMIN":
        details = [item for item in details if item.session.id in principal.session_scope]
    return [_session_detail_view(item, request) for item in details]


@router.patch("/sessions/{session_id}/status", response_model=SessionDetailView)
def update_session_status(
    session_id: str,
    body: UpdateSessionStatusRequest,
    use_case: UpdateExamSessionStatusUseCase,
    request: Request,
) -> SessionDetailView:
    authorize(request, Permission.CONTROL_AGENT, session_id=session_id)
    details = use_case(UpdateExamSessionStatusInput(session_id=session_id, **body.model_dump()))
    return _session_detail_view(details, request)


@router.get("/sessions/{session_id}", response_model=SessionDetailView)
def get_session(
    session_id: str,
    use_case: GetExamSessionUseCase,
    request: Request,
) -> SessionDetailView:
    authorize(request, Permission.VIEW_SESSION, session_id=session_id)
    return _session_detail_view(use_case(session_id), request)


@router.post("/sessions/{session_id}/policy/deploy", response_model=SessionView)
def deploy_policy(
    session_id: str,
    body: DeployPolicyRequest,
    service: Service,
    request: Request,
) -> SessionView:
    current = service.get_session(session_id)
    authorize(
        request,
        Permission.CONTROL_AGENT,
        session_id=session_id,
        session_state=current.state.value,
    )
    session = service.deploy_policy(DeployPolicyInput(session_id=session_id, **body.model_dump()))
    return _session_view(session)


@router.post(
    "/sessions/{session_id}/workstations/{workstation_id}/preflight",
    response_model=SessionView,
)
def submit_preflight(
    session_id: str,
    workstation_id: str,
    body: SubmitPreflightRequest,
    service: Service,
    request: Request,
) -> SessionView:
    authorize(request, Permission.CONTROL_AGENT, session_id=session_id)
    checks = [PreflightCheck(**check.model_dump()) for check in body.checks]
    session = service.submit_preflight(
        SubmitPreflightInput(
            session_id=session_id,
            workstation_id=workstation_id,
            checks=checks,
            actor=body.actor,
        )
    )
    return _session_view(session)


@router.post(
    "/sessions/{session_id}/workstations/{workstation_id}/preflight/retry",
    response_model=PreflightHealthCheckView,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_preflight(
    session_id: str,
    workstation_id: str,
    body: RequestPreflightRequest,
    service: Service,
    request: Request,
) -> PreflightHealthCheckView:
    authorize(request, Permission.CONTROL_AGENT, session_id=session_id)
    command = service.request_preflight(
        RequestPreflightInput(
            session_id=session_id,
            workstation_id=workstation_id,
            actor=body.actor,
        )
    )
    return PreflightHealthCheckView(
        command_id=command.id,
        session_id=command.session_id,
        workstation_id=command.target_id,
        status=command.status.value,
    )


@router.post("/sessions/{session_id}/start", response_model=SessionView)
def start_session(
    session_id: str,
    body: StartSessionRequest,
    service: Service,
    request: Request,
) -> SessionView:
    current = service.get_session(session_id)
    authorize(
        request,
        Permission.CONTROL_AGENT,
        session_id=session_id,
        session_state=current.state.value,
    )
    session = service.start_session(StartSessionInput(session_id=session_id, **body.model_dump()))
    return _session_view(session)


@router.post(
    "/sessions/{session_id}/telemetry",
    response_model=TelemetryAcceptedView,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_telemetry(
    session_id: str,
    body: TelemetryRequest,
    service: Service,
    request: Request,
) -> TelemetryAcceptedView:
    authorize(request, Permission.CONTROL_AGENT, session_id=session_id)
    event, incident_id = service.ingest_telemetry(
        TelemetryInput(session_id=session_id, **body.model_dump())
    )
    return TelemetryAcceptedView(event_id=event.id, incident_id=incident_id)


@router.post("/sessions/{session_id}/finish", response_model=SessionView)
def finish_session(
    session_id: str,
    body: FinishSessionRequest,
    service: Service,
    request: Request,
) -> SessionView:
    current = service.get_session(session_id)
    authorize(
        request,
        Permission.CONTROL_AGENT,
        session_id=session_id,
        session_state=current.state.value,
    )
    return _session_view(service.finish_session(session_id, body.actor))


@router.get("/sessions/{session_id}/summary")
def get_summary(session_id: str, service: Service, request: Request) -> dict:
    authorize(request, Permission.VIEW_INCIDENT, session_id=session_id)
    return service.get_summary(session_id)


def _session_view(session: ExamSession) -> SessionView:
    return SessionView.model_validate(session.to_dict())


def _security_event_view(event: TelemetryEvent) -> SecurityEventView:
    payload = event.payload
    return SecurityEventView(
        event_id=event.id,
        session_id=event.session_id,
        agent_id=event.workstation_id,
        event_type=event.event_type,
        severity=event.severity,
        category=event.category,
        action=event.action,
        destination=event.destination,
        correlation_id=event.correlation_id,
        occurred_at=event.occurred_at,
        source=_optional_string(payload.get("source")),
        process_name=_optional_string(payload.get("process_name")),
        pid=payload.get("pid") if isinstance(payload.get("pid"), int) else None,
        username=_optional_string(payload.get("username")),
        target=_optional_string(payload.get("target")),
        enforcement_result=_optional_string(payload.get("enforcement_result")),
        recovery_status=_optional_string(payload.get("recovery_status")),
        payload=payload,
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _session_detail_view(details: ExamSessionDetails, request: Request) -> SessionDetailView:
    session = details.session
    operational = {
        agent.id: _agent_operational_view(request, agent.id)
        for agent in details.agents
    }
    return SessionDetailView.model_validate(
        {
            **session.to_dict(),
            "name": session.exam_name,
            "room": session.room_id,
            "status": session.state,
            "agent_count": details.agent_count,
            "agents": [
                AssignedAgentView(
                    id=agent.id,
                    hostname=agent.hostname,
                    ip_address=agent.ip_address,
                    agent_version=(
                        operational[agent.id].get("agent_version")
                        or agent.agent_version
                    ),
                    status=agent.status,
                    last_seen=agent.last_seen,
                    assigned_at=agent.assigned_at,
                    policy_status=agent.policy_status,
                    presence_health=operational[agent.id].get("health"),
                    service_health=operational[agent.id].get("service_health"),
                    active_policy_hash=(
                        operational[agent.id].get("active_policy_hash")
                        or session.workstations[agent.id].actual_policy_hash
                    ),
                    gateway_id=operational[agent.id].get("gateway_id"),
                )
                for agent in sorted(details.agents, key=lambda item: item.id)
            ],
            "violations": [
                PolicyViolationView(
                    workstation_id=violation.workstation_id,
                    destination=violation.destination,
                    category=violation.category,
                    action=violation.action,
                    occurred_at=violation.occurred_at,
                )
                for violation in details.violations
            ],
        }
    )


def _agent_operational_view(request: Request, agent_id: str) -> dict:
    presence = request.app.state.presence.get_agent(agent_id) or {}
    if presence.get("gateway_id"):
        return presence
    binding = request.app.state.container.resolve_gateway_for_agent(agent_id)
    if binding is None:
        return presence
    return {**presence, "gateway_id": binding.gateway_id}
