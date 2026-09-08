from __future__ import annotations

from fastapi import APIRouter, status

from app.application.dtos.exam_pipeline import (
    CreateSessionInput,
    DeployPolicyInput,
    StartSessionInput,
    SubmitPreflightInput,
    TelemetryInput,
)
from app.application.dtos.session_management import (
    CreateExamSessionInput,
    ExamSessionDetails,
    UpdateExamSessionStatusInput,
)
from app.domain.entities.exam_session import ExamSession, PreflightCheck
from app.presentation.api.deps import (
    CreateExamSessionUseCase,
    GetExamSessionUseCase,
    ListExamSessionsUseCase,
    Service,
    UpdateExamSessionStatusUseCase,
)
from app.presentation.schemas.exam_pipeline import (
    AssignedAgentView,
    CreateManagementSessionRequest,
    CreatePipelineSessionRequest,
    DeployPolicyRequest,
    FinishSessionRequest,
    PolicyViolationView,
    SessionDetailView,
    SessionView,
    StartSessionRequest,
    SubmitPreflightRequest,
    TelemetryAcceptedView,
    TelemetryRequest,
    UpdateSessionStatusRequest,
)

router = APIRouter(prefix="/api/v1", tags=["exam-pipeline"])


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
) -> SessionDetailView:
    if isinstance(body, CreatePipelineSessionRequest):
        session = service.create_session(CreateSessionInput(**body.model_dump()))
    else:
        details = create_management(CreateExamSessionInput(**body.model_dump()))
        session = details.session
    return _session_detail_view(get_management(session.id))


@router.get("/sessions", response_model=list[SessionDetailView])
def list_sessions(use_case: ListExamSessionsUseCase) -> list[SessionDetailView]:
    return [_session_detail_view(details) for details in use_case()]


@router.patch("/sessions/{session_id}/status", response_model=SessionDetailView)
def update_session_status(
    session_id: str,
    body: UpdateSessionStatusRequest,
    use_case: UpdateExamSessionStatusUseCase,
) -> SessionDetailView:
    details = use_case(UpdateExamSessionStatusInput(session_id=session_id, **body.model_dump()))
    return _session_detail_view(details)


@router.get("/sessions/{session_id}", response_model=SessionDetailView)
def get_session(
    session_id: str,
    use_case: GetExamSessionUseCase,
) -> SessionDetailView:
    return _session_detail_view(use_case(session_id))


@router.post("/sessions/{session_id}/policy/deploy", response_model=SessionView)
def deploy_policy(
    session_id: str,
    body: DeployPolicyRequest,
    service: Service,
) -> SessionView:
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
) -> SessionView:
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


@router.post("/sessions/{session_id}/start", response_model=SessionView)
def start_session(
    session_id: str,
    body: StartSessionRequest,
    service: Service,
) -> SessionView:
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
) -> TelemetryAcceptedView:
    event, incident_id = service.ingest_telemetry(
        TelemetryInput(session_id=session_id, **body.model_dump())
    )
    return TelemetryAcceptedView(event_id=event.id, incident_id=incident_id)


@router.post("/sessions/{session_id}/finish", response_model=SessionView)
def finish_session(
    session_id: str,
    body: FinishSessionRequest,
    service: Service,
) -> SessionView:
    return _session_view(service.finish_session(session_id, body.actor))


@router.get("/sessions/{session_id}/summary")
def get_summary(session_id: str, service: Service) -> dict:
    return service.get_summary(session_id)


def _session_view(session: ExamSession) -> SessionView:
    return SessionView.model_validate(session.to_dict())


def _session_detail_view(details: ExamSessionDetails) -> SessionDetailView:
    session = details.session
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
                    status=agent.status,
                    last_seen=agent.last_seen,
                    assigned_at=agent.assigned_at,
                    policy_status=agent.policy_status,
                )
                for agent in sorted(details.agents, key=lambda item: item.id)
            ],
            "violations": [
                PolicyViolationView(
                    workstation_id=violation.workstation_id,
                    destination=violation.destination,
                    category=violation.category,
                    occurred_at=violation.occurred_at,
                )
                for violation in details.violations
            ],
        }
    )
