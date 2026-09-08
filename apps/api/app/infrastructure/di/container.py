from __future__ import annotations

from dataclasses import dataclass

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
from app.config import Settings
from app.infrastructure.persistence.database import SqliteDatabase


@dataclass(frozen=True, slots=True)
class Container:
    database: SqliteDatabase
    pipeline_service: ExamPipelineService
    register_agent: RegisterAgent
    heartbeat_agent: HeartbeatAgent
    list_agents: ListAgents
    create_exam_session: CreateExamSession
    get_exam_session: GetExamSession
    list_exam_sessions: ListExamSessions
    update_exam_session_status: UpdateExamSessionStatus
    list_policy_profiles: ListPolicyProfiles
    create_policy_profile: CreatePolicyProfile
    update_policy_profile: UpdatePolicyProfile
    delete_policy_profile: DeletePolicyProfile
    get_pending_commands: GetPendingCommands
    acknowledge_command: AcknowledgeCommand


def build_container(settings: Settings) -> Container:
    database = SqliteDatabase(settings.database_path)
    database.initialize()
    return Container(
        database=database,
        pipeline_service=ExamPipelineService(database.unit_of_work),
        register_agent=RegisterAgent(database.unit_of_work),
        heartbeat_agent=HeartbeatAgent(database.unit_of_work),
        list_agents=ListAgents(database.unit_of_work),
        create_exam_session=CreateExamSession(database.unit_of_work),
        get_exam_session=GetExamSession(database.unit_of_work),
        list_exam_sessions=ListExamSessions(database.unit_of_work),
        update_exam_session_status=UpdateExamSessionStatus(database.unit_of_work),
        list_policy_profiles=ListPolicyProfiles(database.unit_of_work),
        create_policy_profile=CreatePolicyProfile(database.unit_of_work),
        update_policy_profile=UpdatePolicyProfile(database.unit_of_work),
        delete_policy_profile=DeletePolicyProfile(database.unit_of_work),
        get_pending_commands=GetPendingCommands(database.unit_of_work),
        acknowledge_command=AcknowledgeCommand(database.unit_of_work),
    )
