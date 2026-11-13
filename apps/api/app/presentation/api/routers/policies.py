from fastapi import APIRouter, HTTPException, Request, Response, status

from app.application.dtos.policies import (
    AcknowledgeCommandInput,
    CreatePolicyProfileInput,
    PolicyProfileDetails,
    UpdatePolicyProfileInput,
)
from app.application.security import Permission
from app.domain.entities.exam_session import ExamSession
from app.domain.entities.operations import Command
from app.presentation.api.deps import (
    AcknowledgeCommandUseCase,
    CreatePolicyProfileUseCase,
    DeletePolicyProfileUseCase,
    ListPolicyProfilesUseCase,
    ListTargetCommandsUseCase,
    UpdatePolicyProfileUseCase,
    actor_name,
    authorize,
    deny_scope,
    scoped_resources,
)
from app.presentation.schemas.exam_pipeline import SessionView
from app.presentation.schemas.policies import (
    AcknowledgeCommandRequest,
    CommandView,
    CreatePolicyProfileRequest,
    PolicyProfileView,
    UpdatePolicyProfileRequest,
)
from app.presentation.serializers.policy_yaml import render_policy_yaml

router = APIRouter(prefix="/api/v1", tags=["policies"])


@router.get("/policy-profiles", response_model=list[PolicyProfileView])
def policy_profiles(
    use_case: ListPolicyProfilesUseCase, request: Request
) -> list[PolicyProfileView]:
    authorize(request, Permission.VIEW_SESSION)
    return [_policy_profile_view(profile) for profile in use_case()]


def _record_policy_profile_audit(
    request: Request, action: str, profile_id: str, reason_code: str
) -> None:
    principal = getattr(request.state, "principal", None)
    request.app.state.container.security_audit.record(
        action=action,
        actor=actor_name(request, "admin"),
        actor_type=principal.role.value if principal is not None else "ADMIN",
        resource_type="POLICY_PROFILE",
        resource_id=profile_id,
        reason_code=reason_code,
        correlation_id=request.headers.get("x-correlation-id"),
    )


@router.post(
    "/policy-profiles",
    response_model=PolicyProfileView,
    status_code=status.HTTP_201_CREATED,
)
def create_policy_profile(
    body: CreatePolicyProfileRequest,
    use_case: CreatePolicyProfileUseCase,
    request: Request,
) -> PolicyProfileView:
    authorize(request, Permission.MANAGE_POLICY)
    profile = use_case(CreatePolicyProfileInput(**body.model_dump()))
    _record_policy_profile_audit(
        request, "POLICY_PROFILE_CREATED", profile.id, "CREATED"
    )
    return _policy_profile_view(profile)


@router.put("/policy-profiles/{profile_id}", response_model=PolicyProfileView)
def update_policy_profile(
    profile_id: str,
    body: UpdatePolicyProfileRequest,
    use_case: UpdatePolicyProfileUseCase,
    request: Request,
) -> PolicyProfileView:
    authorize(request, Permission.MANAGE_POLICY)
    profile = use_case(
        UpdatePolicyProfileInput(id=profile_id, **body.model_dump())
    )
    _record_policy_profile_audit(
        request, "POLICY_PROFILE_UPDATED", profile.id, "UPDATED"
    )
    return _policy_profile_view(profile)


@router.delete(
    "/policy-profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_policy_profile(
    profile_id: str,
    use_case: DeletePolicyProfileUseCase,
    request: Request,
) -> Response:
    authorize(request, Permission.MANAGE_POLICY)
    use_case(profile_id)
    _record_policy_profile_audit(
        request, "POLICY_PROFILE_DELETED", profile_id.strip().upper(), "DELETED"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/agents/{target_id}/commands", response_model=list[CommandView])
def pending_commands(
    target_id: str, use_case: ListTargetCommandsUseCase, request: Request
) -> list[CommandView]:
    authorize(request, Permission.CONTROL_AGENT)
    normalized_target = target_id.strip()
    scope = scoped_resources(request)
    principal = getattr(request.state, "principal", None)
    commands = use_case(normalized_target)
    if scope is not None and principal is not None:
        allowed_agents, allowed_gateways = scope
        if (
            normalized_target not in allowed_agents
            and normalized_target not in allowed_gateways
        ):
            deny_scope(
                request,
                "COMMAND_TARGET",
                normalized_target,
                "target is outside examiner session scope",
                "TARGET_OUTSIDE_SESSION_SCOPE",
            )
        commands = [
            command
            for command in commands
            if command.session_id in principal.session_scope
        ]
    return [_command_view(command) for command in commands]


@router.post("/commands/{command_id}/acknowledge", response_model=SessionView)
def acknowledge_command(
    command_id: str,
    body: AcknowledgeCommandRequest,
    use_case: AcknowledgeCommandUseCase,
    request: Request,
) -> SessionView:
    if request.app.state.settings.environment == "production-like":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "direct HTTP acknowledgement is disabled in production-like environment; "
            "use Gateway WebSocket",
        )
    authorize(request, Permission.CONTROL_AGENT)
    session = use_case(
        AcknowledgeCommandInput(
            command_id=command_id,
            success=body.success,
            policy_hash=body.policy_hash,
            error=body.error,
            actor=body.actor,
            auto_deliver=True,
        )
    )
    return _session_view(session)


def _session_view(session: ExamSession) -> SessionView:
    return SessionView.model_validate(session.to_dict())


def _command_view(command: Command) -> CommandView:
    return CommandView(
        id=command.id,
        session_id=command.session_id,
        target_id=command.target_id,
        type=command.type,
        payload=command.payload,
        status=command.status,
        created_at=command.created_at,
        attempt_count=command.attempt_count,
        last_attempt_at=command.last_attempt_at,
        next_retry_at=command.next_retry_at,
        expires_at=command.expires_at,
    )


def _policy_profile_view(profile: PolicyProfileDetails) -> PolicyProfileView:
    return PolicyProfileView(
        id=profile.id,
        label=profile.label,
        description=profile.description,
        rules=profile.rules,
        is_builtin=profile.is_builtin,
        yaml=render_policy_yaml(profile.id, profile.rules),
    )
