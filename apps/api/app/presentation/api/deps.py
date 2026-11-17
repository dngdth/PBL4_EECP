from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.application.security import AuthorizationDeniedError, Permission, Principal, Role
from app.application.use_cases.agents.management import (
    HeartbeatAgent,
    ListAgents,
    RegisterAgent,
)
from app.application.use_cases.exam_sessions.management import (
    CreateExamSession,
    GetExamSession,
    ListExamSessions,
    UpdateExamSessionStatus,
)
from app.application.use_cases.exam_sessions.pipeline import ExamPipelineService
from app.application.use_cases.policies.management import (
    AcknowledgeCommand,
    CreatePolicyProfile,
    DeletePolicyProfile,
    GetPendingCommands,
    ListPolicyProfiles,
    ListTargetCommands,
    UpdatePolicyProfile,
)
from app.application.use_cases.users.management import (
    AssignSessionExaminers,
    CreateUser,
    ListUsers,
    UpdateUser,
)
from app.domain.entities.user import UserRole


def get_pipeline_service(request: Request) -> ExamPipelineService:
    return request.app.state.container.pipeline_service


Service = Annotated[ExamPipelineService, Depends(get_pipeline_service)]


def get_register_agent(request: Request) -> RegisterAgent:
    return request.app.state.container.register_agent


def get_heartbeat_agent(request: Request) -> HeartbeatAgent:
    return request.app.state.container.heartbeat_agent


def get_list_agents(request: Request) -> ListAgents:
    return request.app.state.container.list_agents


RegisterAgentUseCase = Annotated[RegisterAgent, Depends(get_register_agent)]
HeartbeatAgentUseCase = Annotated[HeartbeatAgent, Depends(get_heartbeat_agent)]
ListAgentsUseCase = Annotated[ListAgents, Depends(get_list_agents)]


def get_create_exam_session(request: Request) -> CreateExamSession:
    return request.app.state.container.create_exam_session


def get_get_exam_session(request: Request) -> GetExamSession:
    return request.app.state.container.get_exam_session


def get_list_exam_sessions(request: Request) -> ListExamSessions:
    return request.app.state.container.list_exam_sessions


def get_update_exam_session_status(request: Request) -> UpdateExamSessionStatus:
    return request.app.state.container.update_exam_session_status


CreateExamSessionUseCase = Annotated[CreateExamSession, Depends(get_create_exam_session)]
GetExamSessionUseCase = Annotated[GetExamSession, Depends(get_get_exam_session)]
ListExamSessionsUseCase = Annotated[ListExamSessions, Depends(get_list_exam_sessions)]
UpdateExamSessionStatusUseCase = Annotated[
    UpdateExamSessionStatus,
    Depends(get_update_exam_session_status),
]


def get_list_policy_profiles(request: Request) -> ListPolicyProfiles:
    return request.app.state.container.list_policy_profiles


def get_list_target_commands(request: Request) -> ListTargetCommands:
    return request.app.state.container.list_target_commands


def get_pending_commands(request: Request) -> GetPendingCommands:
    return request.app.state.container.get_pending_commands


def get_create_policy_profile(request: Request) -> CreatePolicyProfile:
    return request.app.state.container.create_policy_profile


def get_update_policy_profile(request: Request) -> UpdatePolicyProfile:
    return request.app.state.container.update_policy_profile


def get_delete_policy_profile(request: Request) -> DeletePolicyProfile:
    return request.app.state.container.delete_policy_profile


def get_acknowledge_command(request: Request) -> AcknowledgeCommand:
    return request.app.state.container.acknowledge_command


ListPolicyProfilesUseCase = Annotated[
    ListPolicyProfiles, Depends(get_list_policy_profiles)
]
CreatePolicyProfileUseCase = Annotated[
    CreatePolicyProfile, Depends(get_create_policy_profile)
]
UpdatePolicyProfileUseCase = Annotated[
    UpdatePolicyProfile, Depends(get_update_policy_profile)
]
DeletePolicyProfileUseCase = Annotated[
    DeletePolicyProfile, Depends(get_delete_policy_profile)
]
ListTargetCommandsUseCase = Annotated[
    ListTargetCommands, Depends(get_list_target_commands)
]
GetPendingCommandsUseCase = Annotated[
    GetPendingCommands, Depends(get_pending_commands)
]
AcknowledgeCommandUseCase = Annotated[
    AcknowledgeCommand, Depends(get_acknowledge_command)
]


def get_create_user(request: Request) -> CreateUser:
    return request.app.state.container.create_user


def get_list_users(request: Request) -> ListUsers:
    return request.app.state.container.list_users


def get_update_user(request: Request) -> UpdateUser:
    return request.app.state.container.update_user


def get_assign_session_examiners(request: Request) -> AssignSessionExaminers:
    return request.app.state.container.assign_session_examiners


CreateUserUseCase = Annotated[CreateUser, Depends(get_create_user)]
ListUsersUseCase = Annotated[ListUsers, Depends(get_list_users)]
UpdateUserUseCase = Annotated[UpdateUser, Depends(get_update_user)]
AssignSessionExaminersUseCase = Annotated[
    AssignSessionExaminers, Depends(get_assign_session_examiners)
]


def auth_enforced(settings: object) -> bool:
    return bool(
        getattr(settings, "enforce_auth", False)
        or getattr(settings, "environment", "") == "production-like"
    )


def actor_name(request: Request, fallback: str) -> str:
    principal = getattr(request.state, "principal", None)
    if principal is not None and principal.subject:
        return principal.subject
    return fallback


def hydrate_principal(app: object, principal: Principal) -> Principal:
    if principal.expires_at is not None and datetime.now(UTC) >= principal.expires_at:
        raise ValueError("token expired")
    revocation = getattr(app.state, "token_revocation", None)  # type: ignore[attr-defined]
    if revocation is not None and principal.jti and revocation.is_revoked(principal.jti):
        raise ValueError("token revoked")
    if principal.user_id is None:
        return principal
    container = app.state.container  # type: ignore[attr-defined]
    with container.database.unit_of_work() as uow:
        user = uow.users.find(principal.user_id)
        if user is None or not user.is_active:
            raise ValueError("account is inactive or removed")
        scope = (
            frozenset()
            if user.role == UserRole.ADMIN
            else uow.users.list_session_ids_for_user(user.id)
        )
        uow.commit()
    return Principal(
        subject=user.username,
        role=Role(user.role.value),
        session_scope=scope,
        user_id=user.id,
        full_name=user.full_name,
        jti=principal.jti,
        expires_at=principal.expires_at,
    )


def authorize(
    request: Request,
    permission: Permission,
    *,
    session_id: str | None = None,
    session_state: str | None = None,
) -> None:
    if not auth_enforced(request.app.state.settings):
        return
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    try:
        request.app.state.authorization.require(
            principal,
            permission,
            session_id=session_id,
            session_state=session_state,
        )
    except AuthorizationDeniedError as exc:
        request.app.state.container.security_audit.record(
            action="AUTHORIZATION_DENIED",
            actor=principal.subject,
            actor_type=principal.role.value,
            resource_type="SESSION" if session_id else "API_RESOURCE",
            resource_id=session_id or permission.value,
            reason_code=str(exc).upper().replace(" ", "_")[:128],
            # A denied request must not require the target session to exist and
            # must not attach an out-of-scope resource to its per-session chain.
            session_id=None,
            correlation_id=request.headers.get("x-correlation-id"),
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def scoped_resources(
    request: Request,
) -> tuple[frozenset[str], frozenset[str]] | None:
    """Return `(agent_ids, gateway_ids)` visible to a scoped principal, or `None`."""
    if not auth_enforced(request.app.state.settings):
        return None
    principal = getattr(request.state, "principal", None)
    if principal is None or principal.role == Role.ADMIN:
        return None
    agent_ids: set[str] = set()
    gateway_ids: set[str] = set()
    with request.app.state.container.database.unit_of_work() as uow:
        for session in uow.sessions.list_all():
            if session.id in principal.session_scope:
                agent_ids.update(session.workstations.keys())
                if session.gateway_id:
                    gateway_ids.add(session.gateway_id)
        uow.commit()
    return frozenset(agent_ids), frozenset(gateway_ids)


def deny_scope(
    request: Request,
    resource_type: str,
    resource_id: str,
    detail: str,
    reason_code: str,
) -> None:
    principal = getattr(request.state, "principal", None)
    request.app.state.container.security_audit.record(
        action="AUTHORIZATION_DENIED",
        actor=principal.subject if principal is not None else "anonymous",
        actor_type=principal.role.value if principal is not None else "ANONYMOUS",
        resource_type=resource_type,
        resource_id=resource_id,
        reason_code=reason_code[:128],
        correlation_id=request.headers.get("x-correlation-id"),
    )
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail)
