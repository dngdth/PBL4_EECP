from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.application.dtos.policies import (
    AcknowledgeCommandInput,
    CreatePolicyProfileInput,
    PolicyProfileDetails,
    UpdatePolicyProfileInput,
)
from app.domain.entities.exam_session import ExamSession
from app.domain.entities.operations import Command
from app.domain.exceptions.errors import (
    InvalidStateTransitionError,
    PolicyInUseError,
    PolicyValidationError,
)
from app.domain.interfaces.unit_of_work import UnitOfWork, UnitOfWorkFactory
from app.domain.services.policy_profiles import PolicyProfileDefinition
from app.domain.value_objects.enums import CommandStatus, CommandType
from app.domain.value_objects.primitives import utc_now

Clock = Callable[[], datetime]


class ListPolicyProfiles:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def __call__(self) -> list[PolicyProfileDetails]:
        with self._uow_factory() as uow:
            return [_profile_details(profile) for profile in uow.policy_profiles.list_all()]


class CreatePolicyProfile:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def __call__(self, data: CreatePolicyProfileInput) -> PolicyProfileDetails:
        profile = PolicyProfileDefinition(
            id=data.id,
            label=data.label,
            description=data.description,
            rules=data.rules,
        )
        with self._uow_factory() as uow:
            if uow.policy_profiles.find(profile.id) is not None:
                raise PolicyValidationError(
                    f"policy profile already exists: {profile.id}"
                )
            uow.policy_profiles.add(profile)
            uow.commit()
        return _profile_details(profile)


class UpdatePolicyProfile:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def __call__(self, data: UpdatePolicyProfileInput) -> PolicyProfileDetails:
        with self._uow_factory() as uow:
            current = uow.policy_profiles.get(data.id.strip().upper())
            updated = current.editable_copy(
                label=data.label,
                description=data.description,
                rules=data.rules,
            )
            uow.policy_profiles.save(updated)
            uow.commit()
        return _profile_details(updated)


class DeletePolicyProfile:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def __call__(self, profile_id: str) -> None:
        normalized_id = profile_id.strip().upper()
        with self._uow_factory() as uow:
            profile = uow.policy_profiles.get(normalized_id)
            if profile.is_builtin:
                raise PolicyValidationError("built-in policy profiles cannot be deleted")
            used_by = [
                session.id
                for session in uow.sessions.list_all()
                if session.policy is not None and session.policy.profile == normalized_id
            ]
            if used_by:
                raise PolicyInUseError(
                    f"policy profile is used by sessions: {', '.join(sorted(used_by))}"
                )
            uow.policy_profiles.delete(normalized_id)
            uow.commit()


class GetPendingCommands:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self, target_id: str) -> list[Command]:
        at = self._clock()
        delivered: list[Command] = []
        with self._uow_factory() as uow:
            commands = uow.commands.available_for_target(target_id.strip(), at)
            for command in commands:
                if command.should_timeout(at):
                    command.time_out()
                    _record_timeout(uow, command)
                else:
                    command.deliver(at)
                    uow.commands.save(command)
                    delivered.append(command)
            uow.commit()
        return delivered

class AcknowledgeCommand:
    """Acknowledge policy commands for either direct or gateway sessions."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self, data: AcknowledgeCommandInput) -> ExamSession:
        at = self._clock()
        timed_out = False
        with self._uow_factory() as uow:
            command = uow.commands.get(data.command_id.strip())
            if command.status != CommandStatus.DELIVERED:
                raise InvalidStateTransitionError(
                    "command is not awaiting acknowledgement"
                )
            session = uow.sessions.get(command.session_id)
            if command.is_expired(at):
                command.time_out()
                _record_timeout(uow, command, session)
                uow.commit()
                timed_out = True
            else:
                command.acknowledged_at = at
                command.next_retry_at = None

                if not data.success:
                    self._record_failure(session, command, data.error)
                    uow.sessions.save(session)
                    uow.commands.save(command)
                    uow.audits.append(
                        session.id,
                        actor=data.actor,
                        action="COMMAND_FAILED",
                        target=command.target_id,
                        details={
                            "command_id": command.id,
                            "type": command.type,
                            "error": command.error,
                        },
                    )
                    uow.commit()
                    return session

                command.status = CommandStatus.ACKNOWLEDGED
                self._record_success(session, command, data.policy_hash)
                uow.sessions.save(session)
                uow.commands.save(command)
                uow.audits.append(
                    session.id,
                    actor=data.actor,
                    action="COMMAND_ACKNOWLEDGED",
                    target=command.target_id,
                    details={"command_id": command.id, "type": command.type},
                )
                uow.commit()
                return session

        if timed_out:
            raise InvalidStateTransitionError("command acknowledgement timed out")
        raise RuntimeError("unreachable command acknowledgement state")

    @staticmethod
    def _record_failure(
        session: ExamSession, command: Command, error: str | None
    ) -> None:
        command.status = CommandStatus.FAILED
        command.error = error or "target reported command failure"
        if command.type != CommandType.APPLY_POLICY:
            return
        if session.gateway_id is None:
            session.record_management_policy_failure(command.target_id)
        else:
            session.record_policy_failure(command.target_id)

    @staticmethod
    def _record_success(
        session: ExamSession, command: Command, policy_hash: str | None
    ) -> None:
        if command.type == CommandType.APPLY_POLICY:
            if not policy_hash:
                raise PolicyValidationError(
                    "APPLY_POLICY acknowledgement requires policy_hash"
                )
            if session.gateway_id is None:
                session.acknowledge_management_policy(command.target_id, policy_hash)
            else:
                session.acknowledge_policy(command.target_id, policy_hash)
        elif command.type == CommandType.RESTORE_BASELINE:
            if session.gateway_id is None:
                session.acknowledge_management_restore(command.target_id)
            else:
                session.acknowledge_restore(command.target_id)


def _record_timeout(
    uow: UnitOfWork,
    command: Command,
    session: ExamSession | None = None,
) -> None:
    current_session = session or uow.sessions.get(command.session_id)
    if command.type == CommandType.APPLY_POLICY:
        if current_session.gateway_id is None:
            current_session.record_management_policy_failure(command.target_id)
        else:
            current_session.record_policy_failure(command.target_id)
        uow.sessions.save(current_session)
    uow.commands.save(command)
    uow.audits.append(
        current_session.id,
        actor="control-server",
        action="COMMAND_TIMED_OUT",
        target=command.target_id,
        details={
            "command_id": command.id,
            "type": command.type,
            "attempt_count": command.attempt_count,
        },
    )


def _profile_details(profile: PolicyProfileDefinition) -> PolicyProfileDetails:
    return PolicyProfileDetails(
        id=profile.id,
        label=profile.label,
        description=profile.description,
        rules=profile.rules,
        is_builtin=profile.is_builtin,
    )
