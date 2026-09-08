from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application.dtos.policies import AcknowledgeCommandInput
from app.application.dtos.session_management import CreateExamSessionInput
from app.application.use_cases.exam_sessions.management import CreateExamSession
from app.application.use_cases.policies.management import (
    AcknowledgeCommand,
    GetPendingCommands,
)
from app.domain.entities.agent import Agent
from app.domain.exceptions.errors import InvalidStateTransitionError
from app.domain.value_objects.enums import CommandStatus, Readiness
from app.infrastructure.persistence.database import SqliteDatabase

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)


def _database(tmp_path: Path) -> SqliteDatabase:
    database = SqliteDatabase(tmp_path / "command-delivery.db")
    database.initialize()
    with database.unit_of_work() as uow:
        uow.agents.add(
            Agent.register("PC01", "HOST-PC01", "192.168.3.55", "1.0.0", NOW)
        )
        uow.commit()
    return database


def test_command_is_retried_three_times_then_times_out(tmp_path: Path) -> None:
    database = _database(tmp_path)
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )
    current = [NOW]
    get_commands = GetPendingCommands(database.unit_of_work, clock=lambda: current[0])

    first = get_commands("PC01")
    assert len(first) == 1
    command_id = first[0].id
    assert first[0].status == CommandStatus.DELIVERED
    assert first[0].attempt_count == 1
    assert first[0].next_retry_at == NOW + timedelta(seconds=10)

    current[0] = NOW + timedelta(seconds=9)
    assert get_commands("PC01") == []

    current[0] = NOW + timedelta(seconds=10)
    assert get_commands("PC01")[0].attempt_count == 2
    current[0] = NOW + timedelta(seconds=20)
    assert get_commands("PC01")[0].attempt_count == 3

    current[0] = NOW + timedelta(seconds=30)
    assert get_commands("PC01") == []

    with database.unit_of_work() as uow:
        command = uow.commands.get(command_id)
        session = uow.sessions.get(created.session.id)
        audits = uow.audits.list_for_session(created.session.id)

    assert command.status == CommandStatus.TIMED_OUT
    assert command.error == "command acknowledgement timed out"
    assert command.acknowledged_at is None
    assert session.workstations["PC01"].readiness == Readiness.FAILED
    assert audits[-1].action == "COMMAND_TIMED_OUT"


def test_command_expires_even_before_first_delivery(tmp_path: Path) -> None:
    database = _database(tmp_path)
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )

    commands = GetPendingCommands(
        database.unit_of_work,
        clock=lambda: NOW + timedelta(minutes=1),
    )("PC01")

    assert commands == []
    with database.unit_of_work() as uow:
        session = uow.sessions.get(created.session.id)
        assert session.workstations["PC01"].readiness == Readiness.FAILED


def test_acknowledgement_after_deadline_is_rejected_and_persisted(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )
    command = GetPendingCommands(database.unit_of_work, clock=lambda: NOW)("PC01")[0]

    with pytest.raises(InvalidStateTransitionError, match="timed out"):
        AcknowledgeCommand(
            database.unit_of_work,
            clock=lambda: NOW + timedelta(minutes=1),
        )(
            AcknowledgeCommandInput(
                command_id=command.id,
                success=True,
                policy_hash=command.payload["policy_hash"],
                actor="PC01",
            )
        )

    with database.unit_of_work() as uow:
        persisted = uow.commands.get(command.id)
        session = uow.sessions.get(created.session.id)

    assert persisted.status == CommandStatus.TIMED_OUT
    assert session.workstations["PC01"].readiness == Readiness.FAILED
