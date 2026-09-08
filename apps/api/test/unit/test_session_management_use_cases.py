from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application.dtos.exam_pipeline import CreateSessionInput
from app.application.dtos.policies import AcknowledgeCommandInput
from app.application.dtos.session_management import (
    CreateExamSessionInput,
    UpdateExamSessionStatusInput,
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
    GetPendingCommands,
)
from app.domain.entities.agent import Agent
from app.domain.exceptions.errors import (
    EntityNotFoundError,
    InvalidStateTransitionError,
    PolicyValidationError,
    ReadinessGateError,
    SessionConflictError,
)
from app.domain.value_objects.enums import AgentStatus, SessionState
from app.infrastructure.persistence.database import SqliteDatabase

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


def _database(tmp_path: Path) -> SqliteDatabase:
    database = SqliteDatabase(tmp_path / "management.db")
    database.initialize()
    return database


def _register(database: SqliteDatabase, agent_id: str, last_seen=NOW) -> None:
    with database.unit_of_work() as uow:
        uow.agents.add(
            Agent.register(agent_id, f"HOST-{agent_id}", "192.168.3.55", "1.0.0", last_seen)
        )
        uow.commit()


def _apply_policy(database: SqliteDatabase, agent_id: str, at=NOW) -> None:
    command = GetPendingCommands(database.unit_of_work, clock=lambda: at)(agent_id)[0]
    AcknowledgeCommand(database.unit_of_work, clock=lambda: at)(
        AcknowledgeCommandInput(
            command_id=command.id,
            success=True,
            policy_hash=command.payload["policy_hash"],
            actor=agent_id,
        )
    )


def test_create_session_assigns_online_agents(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _register(database, "PC01")
    _register(database, "PC02")

    details = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("PBL4 Final", "A101", ["PC01", "PC02"])
    )

    assert details.session.state == SessionState.CREATED
    assert details.agent_count == 2
    assert [agent.id for agent in details.agents] == ["PC01", "PC02"]


def test_create_session_rejects_unknown_agent(tmp_path: Path) -> None:
    database = _database(tmp_path)

    with pytest.raises(EntityNotFoundError, match="PC99"):
        CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
            CreateExamSessionInput("Exam", "A101", ["PC99"])
        )


def test_create_session_rejects_offline_agent(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _register(database, "PC01", NOW - timedelta(seconds=16))

    with pytest.raises(ReadinessGateError, match="PC01"):
        CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
            CreateExamSessionInput("Exam", "A101", ["PC01"])
        )

    with database.unit_of_work() as uow:
        assert uow.agents.get("PC01").status == AgentStatus.OFFLINE
        assert uow.sessions.list_all() == []


def test_create_session_normalizes_agent_ids_before_lookup(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _register(database, "PC01")

    details = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", [" PC01 "])
    )

    assert [agent.id for agent in details.agents] == ["PC01"]


def test_create_session_validates_duplicate_unknown_agents_before_lookup(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)

    with pytest.raises(PolicyValidationError, match="unique"):
        CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
            CreateExamSessionInput("Exam", "A101", ["PC99", " PC99 "])
        )


def test_create_session_rejects_duplicate_offline_agents_without_liveness_refresh(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _register(database, "PC01", NOW - timedelta(seconds=16))

    with pytest.raises(PolicyValidationError, match="unique"):
        CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
            CreateExamSessionInput("Exam", "A101", ["PC01", " PC01 "])
        )

    with database.unit_of_work() as uow:
        assert uow.agents.get("PC01").status == AgentStatus.ONLINE
        assert uow.sessions.list_all() == []


@pytest.mark.parametrize(
    ("name", "room"),
    [(" ", "A101"), ("Exam", " ")],
)
def test_create_session_validates_name_and_room_before_unknown_agent_lookup(
    tmp_path: Path,
    name: str,
    room: str,
) -> None:
    database = _database(tmp_path)

    with pytest.raises(PolicyValidationError, match="name and room"):
        CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
            CreateExamSessionInput(name, room, ["PC99"])
        )


def test_create_session_rejects_blank_agent_item_without_liveness_refresh(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _register(database, "PC01", NOW - timedelta(seconds=16))

    with pytest.raises(PolicyValidationError, match="empty"):
        CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
            CreateExamSessionInput("Exam", "A101", ["PC01", " "])
        )

    with database.unit_of_work() as uow:
        assert uow.agents.get("PC01").status == AgentStatus.ONLINE
        assert uow.sessions.list_all() == []


def test_get_session_returns_current_agent_and_assignment_details(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _register(database, "PC01")
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )
    refreshed_at = NOW + timedelta(seconds=5)
    with database.unit_of_work() as uow:
        agent = uow.agents.get("PC01")
        agent.reregister("CURRENT-PC01", "192.168.3.99", "1.1.0", refreshed_at)
        uow.agents.save(agent)
        uow.commit()

    details = GetExamSession(database.unit_of_work, clock=lambda: refreshed_at)(created.session.id)

    assert details.agent_count == 1
    assert details.agents[0].id == "PC01"
    assert details.agents[0].hostname == "CURRENT-PC01"
    assert details.agents[0].ip_address == "192.168.3.99"
    assert details.agents[0].status == AgentStatus.ONLINE
    assert details.agents[0].last_seen == refreshed_at
    assert details.agents[0].assigned_at == NOW


def test_list_sessions_returns_newest_first(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _register(database, "PC01")
    _register(database, "PC02")
    create = CreateExamSession(database.unit_of_work, clock=lambda: NOW)
    create(CreateExamSessionInput("First", "A101", ["PC01"]))
    create(CreateExamSessionInput("Second", "A102", ["PC02"]))

    details = ListExamSessions(database.unit_of_work, clock=lambda: NOW)()

    assert [item.session.exam_name for item in details] == ["Second", "First"]


def test_create_session_rejects_agent_assigned_to_active_session(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _register(database, "PC01")
    create = CreateExamSession(database.unit_of_work, clock=lambda: NOW)
    first = create(CreateExamSessionInput("First", "A101", ["PC01"]))

    with pytest.raises(SessionConflictError, match=first.session.id):
        create(CreateExamSessionInput("Second", "A102", ["PC01"]))


def test_ready_rejects_policy_without_agent_acknowledgement(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _register(database, "PC01")
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )

    with pytest.raises(ReadinessGateError, match=r"PC01 \(PENDING\)"):
        UpdateExamSessionStatus(database.unit_of_work, clock=lambda: NOW)(
            UpdateExamSessionStatusInput(created.session.id, SessionState.READY)
        )


def test_ready_rejects_and_persists_offline_agent(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _register(database, "PC01")
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )
    with database.unit_of_work() as uow:
        agent = uow.agents.get("PC01")
        agent.last_seen = NOW - timedelta(seconds=16)
        uow.agents.save(agent)
        uow.commit()

    with pytest.raises(ReadinessGateError, match="PC01"):
        UpdateExamSessionStatus(database.unit_of_work, clock=lambda: NOW)(
            UpdateExamSessionStatusInput(created.session.id, SessionState.READY)
        )

    with database.unit_of_work() as uow:
        assert uow.agents.get("PC01").status == AgentStatus.OFFLINE
        assert uow.sessions.get(created.session.id).state == SessionState.CREATED


def test_valid_management_status_updates_succeed_in_order(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _register(database, "PC01")
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )
    _apply_policy(database, "PC01")
    current = [NOW + timedelta(seconds=1)]
    update = UpdateExamSessionStatus(database.unit_of_work, clock=lambda: current[0])

    ready = update(UpdateExamSessionStatusInput(created.session.id, SessionState.READY))
    current[0] = NOW + timedelta(seconds=2)
    running = update(UpdateExamSessionStatusInput(created.session.id, SessionState.RUNNING))
    current[0] = NOW + timedelta(seconds=3)
    finished = update(UpdateExamSessionStatusInput(created.session.id, SessionState.FINISHED))

    assert ready.session.state == SessionState.READY
    assert running.session.state == SessionState.RUNNING
    assert running.session.started_at == NOW + timedelta(seconds=2)
    assert finished.session.state == SessionState.FINISHED
    assert finished.session.finished_at == NOW + timedelta(seconds=3)


def test_running_and_finished_updates_do_not_recheck_agent_liveness(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _register(database, "PC01")
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )
    _apply_policy(database, "PC01")
    UpdateExamSessionStatus(database.unit_of_work, clock=lambda: NOW)(
        UpdateExamSessionStatusInput(created.session.id, SessionState.READY)
    )
    with database.unit_of_work() as uow:
        agent = uow.agents.get("PC01")
        agent.last_seen = NOW - timedelta(seconds=16)
        uow.agents.save(agent)
        uow.commit()

    running = UpdateExamSessionStatus(database.unit_of_work, clock=lambda: NOW)(
        UpdateExamSessionStatusInput(created.session.id, SessionState.RUNNING)
    )
    finished = UpdateExamSessionStatus(database.unit_of_work, clock=lambda: NOW)(
        UpdateExamSessionStatusInput(created.session.id, SessionState.FINISHED)
    )

    assert running.session.state == SessionState.RUNNING
    assert finished.session.state == SessionState.FINISHED
    with database.unit_of_work() as uow:
        assert uow.agents.get("PC01").status == AgentStatus.ONLINE


def test_pipeline_session_rejects_management_status_update(tmp_path: Path) -> None:
    database = _database(tmp_path)
    legacy = ExamPipelineService(database.unit_of_work).create_session(
        CreateSessionInput("Legacy", "A101", "GW01", ["PC99"], "teacher")
    )

    with pytest.raises(InvalidStateTransitionError, match="pipeline"):
        UpdateExamSessionStatus(database.unit_of_work, clock=lambda: NOW)(
            UpdateExamSessionStatusInput(legacy.id, SessionState.READY)
        )


def test_get_legacy_session_tolerates_unregistered_workstation(tmp_path: Path) -> None:
    database = _database(tmp_path)
    legacy = ExamPipelineService(database.unit_of_work).create_session(
        CreateSessionInput("Legacy", "A101", "GW01", ["PC99"], "teacher")
    )

    details = GetExamSession(database.unit_of_work, clock=lambda: NOW)(legacy.id)

    assert details.agent_count == 1
    assert details.agents[0].id == "PC99"
    assert details.agents[0].hostname is None
    assert details.agents[0].ip_address is None
    assert details.agents[0].status is None
    assert details.agents[0].last_seen is None
    assert details.agents[0].assigned_at is None
