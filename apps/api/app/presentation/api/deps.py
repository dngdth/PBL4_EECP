from typing import Annotated

from fastapi import Depends, Request

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
    UpdatePolicyProfile,
)


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
GetPendingCommandsUseCase = Annotated[
    GetPendingCommands, Depends(get_pending_commands)
]
AcknowledgeCommandUseCase = Annotated[
    AcknowledgeCommand, Depends(get_acknowledge_command)
]
