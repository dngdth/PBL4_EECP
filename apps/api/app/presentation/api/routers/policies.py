from fastapi import APIRouter, Response, status

from app.application.dtos.policies import (
    AcknowledgeCommandInput,
    CreatePolicyProfileInput,
    PolicyProfileDetails,
    UpdatePolicyProfileInput,
)
from app.domain.entities.exam_session import ExamSession
from app.domain.entities.operations import Command
from app.presentation.api.deps import (
    AcknowledgeCommandUseCase,
    CreatePolicyProfileUseCase,
    DeletePolicyProfileUseCase,
    GetPendingCommandsUseCase,
    ListPolicyProfilesUseCase,
    UpdatePolicyProfileUseCase,
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
def policy_profiles(use_case: ListPolicyProfilesUseCase) -> list[PolicyProfileView]:
    return [_policy_profile_view(profile) for profile in use_case()]


@router.post(
    "/policy-profiles",
    response_model=PolicyProfileView,
    status_code=status.HTTP_201_CREATED,
)
def create_policy_profile(
    body: CreatePolicyProfileRequest,
    use_case: CreatePolicyProfileUseCase,
) -> PolicyProfileView:
    profile = use_case(CreatePolicyProfileInput(**body.model_dump()))
    return _policy_profile_view(profile)


@router.put("/policy-profiles/{profile_id}", response_model=PolicyProfileView)
def update_policy_profile(
    profile_id: str,
    body: UpdatePolicyProfileRequest,
    use_case: UpdatePolicyProfileUseCase,
) -> PolicyProfileView:
    profile = use_case(
        UpdatePolicyProfileInput(id=profile_id, **body.model_dump())
    )
    return _policy_profile_view(profile)


@router.delete(
    "/policy-profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_policy_profile(
    profile_id: str,
    use_case: DeletePolicyProfileUseCase,
) -> Response:
    use_case(profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/agents/{target_id}/commands", response_model=list[CommandView])
def pending_commands(
    target_id: str, use_case: GetPendingCommandsUseCase
) -> list[CommandView]:
    return [_command_view(command) for command in use_case(target_id)]


@router.post("/commands/{command_id}/acknowledge", response_model=SessionView)
def acknowledge_command(
    command_id: str,
    body: AcknowledgeCommandRequest,
    use_case: AcknowledgeCommandUseCase,
) -> SessionView:
    session = use_case(
        AcknowledgeCommandInput(command_id=command_id, **body.model_dump())
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
