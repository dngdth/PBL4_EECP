from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application.dtos.policies import AcknowledgeCommandInput
from app.application.dtos.session_management import CreateExamSessionInput
from app.application.use_cases.exam_sessions.management import CreateExamSession
from app.application.use_cases.policies.management import (
    AcknowledgeCommand,
    GetPendingCommands,
    ListTargetCommands,
)
from app.domain.entities.agent import Agent
from app.domain.exceptions.errors import InvalidStateTransitionError, PolicyValidationError
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


def test_acknowledgement_by_correct_agent_succeeds(tmp_path: Path) -> None:
    database = _database(tmp_path)
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )
    command = GetPendingCommands(database.unit_of_work, clock=lambda: NOW)("PC01")[0]

    session = AcknowledgeCommand(database.unit_of_work, clock=lambda: NOW)(
        AcknowledgeCommandInput(
            command_id=command.id,
            success=True,
            policy_hash=command.payload["policy_hash"],
            actor="PC01",
        )
    )

    with database.unit_of_work() as uow:
        persisted = uow.commands.get(command.id)
    assert persisted.status == CommandStatus.ACKNOWLEDGED
    assert persisted.acknowledged_at == NOW
    assert session.workstations["PC01"].actual_policy_hash == command.payload["policy_hash"]
    assert session.workstations["PC01"].policy_compliant is True


def test_acknowledgement_by_wrong_agent_is_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )
    command = GetPendingCommands(database.unit_of_work, clock=lambda: NOW)("PC01")[0]

    with pytest.raises(PolicyValidationError, match="command target PC01 does not match actor PC02"):
        AcknowledgeCommand(database.unit_of_work, clock=lambda: NOW)(
            AcknowledgeCommandInput(
                command_id=command.id,
                success=True,
                policy_hash=command.payload["policy_hash"],
                actor="PC02",
            )
        )

    with database.unit_of_work() as uow:
        persisted = uow.commands.get(command.id)
        session = uow.sessions.get(created.session.id)
    assert persisted.status == CommandStatus.DELIVERED
    assert persisted.acknowledged_at is None
    assert session.workstations["PC01"].actual_policy_hash is None


def test_duplicate_acknowledgement_is_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )
    command = GetPendingCommands(database.unit_of_work, clock=lambda: NOW)("PC01")[0]

    ack_input = AcknowledgeCommandInput(
        command_id=command.id,
        success=True,
        policy_hash=command.payload["policy_hash"],
        actor="PC01",
    )
    # First ACK succeeds
    AcknowledgeCommand(database.unit_of_work, clock=lambda: NOW)(ack_input)

    with database.unit_of_work() as uow:
        persisted = uow.commands.get(command.id)
    assert persisted.status == CommandStatus.ACKNOWLEDGED

    # Second ACK (ACK trùng) is rejected
    with pytest.raises(InvalidStateTransitionError, match="command is not awaiting acknowledgement"):
        AcknowledgeCommand(database.unit_of_work, clock=lambda: NOW)(ack_input)

    with database.unit_of_work() as uow:
        persisted_after = uow.commands.get(command.id)
    assert persisted_after.status == CommandStatus.ACKNOWLEDGED


def test_list_target_commands_is_strictly_read_only_and_lifecycle(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    created = CreateExamSession(database.unit_of_work, clock=lambda: NOW)(
        CreateExamSessionInput("Exam", "A101", ["PC01"])
    )
    list_commands = ListTargetCommands(database.unit_of_work)
    get_commands = GetPendingCommands(database.unit_of_work, clock=lambda: NOW)

    # 1. Multiple list queries (e.g. user opening/refreshing CommandQueueModal 5 times)
    # MUST NOT alter status, attempt_count, last_attempt_at, or next_retry_at
    for _ in range(5):
        commands = list_commands("PC01")
        assert len(commands) == 1
        cmd = commands[0]
        assert cmd.status == CommandStatus.PENDING
        assert cmd.attempt_count == 0
        assert cmd.last_attempt_at is None
        assert cmd.next_retry_at is None

    # Check directly from DB that the command is untouched
    with database.unit_of_work() as uow:
        persisted = uow.commands.get(commands[0].id)
        assert persisted.status == CommandStatus.PENDING
        assert persisted.attempt_count == 0
        assert persisted.last_attempt_at is None
        assert persisted.next_retry_at is None

    # An ACK received while the command is still PENDING (not yet dispatched) MUST be rejected
    with pytest.raises(InvalidStateTransitionError, match="command is not awaiting acknowledgement"):
        AcknowledgeCommand(database.unit_of_work, clock=lambda: NOW)(
            AcknowledgeCommandInput(
                command_id=commands[0].id,
                success=True,
                policy_hash=commands[0].payload["policy_hash"],
                actor="PC01",
            )
        )

    # 2. Gateway dispatch retrieves and claims command for delivery
    dispatched = get_commands("PC01")
    assert len(dispatched) == 1
    assert dispatched[0].status == CommandStatus.DELIVERED
    assert dispatched[0].attempt_count == 1
    assert dispatched[0].last_attempt_at == NOW
    assert dispatched[0].next_retry_at == NOW + timedelta(seconds=10)

    # 3. Viewing list after delivery still does not mutate attempt_count or last_attempt_at
    for _ in range(3):
        post_dispatch_list = list_commands("PC01")
        assert len(post_dispatch_list) == 1
        assert post_dispatch_list[0].status == CommandStatus.DELIVERED
        assert post_dispatch_list[0].attempt_count == 1
        assert post_dispatch_list[0].last_attempt_at == NOW

    # 4. Agent acknowledges command -> transitions to ACKNOWLEDGED
    AcknowledgeCommand(database.unit_of_work, clock=lambda: NOW)(
        AcknowledgeCommandInput(
            command_id=dispatched[0].id,
            success=True,
            policy_hash=dispatched[0].payload["policy_hash"],
            actor="PC01",
        )
    )

    with database.unit_of_work() as uow:
        acknowledged_cmd = uow.commands.get(dispatched[0].id)
        assert acknowledged_cmd.status == CommandStatus.ACKNOWLEDGED
        assert acknowledged_cmd.acknowledged_at == NOW

    # 5. Duplicate ACK is rejected
    with pytest.raises(InvalidStateTransitionError, match="command is not awaiting acknowledgement"):
        AcknowledgeCommand(database.unit_of_work, clock=lambda: NOW)(
            AcknowledgeCommandInput(
                command_id=dispatched[0].id,
                success=True,
                policy_hash=dispatched[0].payload["policy_hash"],
                actor="PC01",
            )
        )
