from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.application.dtos.session_management import (
    AssignedAgentDetails,
    CreateExamSessionInput,
    ExamSessionDetails,
    PolicyViolationDetails,
    UpdateExamSessionStatusInput,
)
from app.domain.entities.agent import Agent
from app.domain.entities.exam_session import ExamSession
from app.domain.entities.operations import Command
from app.domain.entities.session_workstation import SessionWorkstation
from app.domain.exceptions.errors import (
    PolicyValidationError,
    ReadinessGateError,
    SessionConflictError,
)
from app.domain.interfaces.unit_of_work import UnitOfWork, UnitOfWorkFactory
from app.domain.value_objects.enums import AgentStatus, CommandType, Readiness, SessionState
from app.domain.value_objects.primitives import utc_now

Clock = Callable[[], datetime]


class CreateExamSession:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock = utc_now,
    ):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self, data: CreateExamSessionInput) -> ExamSessionDetails:
        at = self._clock()
        session = ExamSession.create_managed(data.name, data.room, data.agent_ids, at)
        agent_ids = list(session.workstations)
        offline_ids: list[str] = []
        with self._uow_factory() as uow:
            profile = uow.policy_profiles.find(data.policy_profile)
            if profile is None:
                raise PolicyValidationError(
                    f"unknown policy profile: {data.policy_profile}"
                )
            policy = session.assign_management_policy(profile.id, profile.rules)
            agents = [uow.agents.get(agent_id) for agent_id in agent_ids]
            conflicts = _active_assignment_conflicts(uow, set(agent_ids))
            if conflicts:
                formatted = ", ".join(
                    f"{agent_id} ({session_id})"
                    for agent_id, session_id in sorted(conflicts.items())
                )
                raise SessionConflictError(
                    f"agents already belong to active sessions: {formatted}"
                )
            for agent in agents:
                if agent.refresh_liveness(at):
                    uow.agents.save(agent)
                if agent.status == AgentStatus.OFFLINE:
                    offline_ids.append(agent.id)
            if offline_ids:
                uow.commit()
            else:
                uow.sessions.add(session)
                uow.session_workstations.assign_many(
                    [SessionWorkstation.assign(session.id, agent.id, at) for agent in agents]
                )
                uow.commands.add_many(
                    [
                        Command(
                            session_id=session.id,
                            target_id=agent.id,
                            type=CommandType.APPLY_POLICY,
                            payload={
                                "format": "eecp-policy/v1",
                                "policy_hash": policy.policy_hash,
                                "version": policy.version,
                                "profile": policy.profile,
                                "rules": policy.rules,
                            },
                            created_at=at,
                        )
                        for agent in agents
                    ]
                )
                uow.audits.append(
                    session.id,
                    actor=data.actor,
                    action="SESSION_CREATED",
                    target=session.id,
                    details={
                        "room_id": session.room_id,
                        "workstations": sorted(session.workstations),
                        "policy_profile": policy.profile,
                        "policy_hash": policy.policy_hash,
                    },
                )
                uow.commit()
                return ExamSessionDetails(
                    session=session,
                    agents=[
                        AssignedAgentDetails(
                            id=agent.id,
                            hostname=agent.hostname,
                            ip_address=agent.ip_address,
                            status=agent.status,
                            last_seen=agent.last_seen,
                            assigned_at=at,
                            policy_status="PENDING",
                        )
                        for agent in agents
                    ],
                    violations=[],
                )

        raise ReadinessGateError(f"offline agents cannot be assigned: {', '.join(offline_ids)}")


class GetExamSession:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self, session_id: str) -> ExamSessionDetails:
        normalized_id = session_id.strip()
        at = self._clock()
        with self._uow_factory() as uow:
            session = uow.sessions.get(normalized_id)
            details = _details(uow, session, at)
            uow.commit()
            return details


class ListExamSessions:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self) -> list[ExamSessionDetails]:
        at = self._clock()
        with self._uow_factory() as uow:
            details = [_details(uow, session, at) for session in uow.sessions.list_all()]
            uow.commit()
            return details


class UpdateExamSessionStatus:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self, data: UpdateExamSessionStatusInput) -> ExamSessionDetails:
        session_id = data.session_id.strip()
        at = self._clock()
        offline_ids: list[str] = []
        policy_blocked: list[str] = []
        with self._uow_factory() as uow:
            session = uow.sessions.get(session_id)
            readiness_transition = (
                session.gateway_id is None
                and session.state == SessionState.CREATED
                and data.status == SessionState.READY
            )
            if readiness_transition:
                details = _details(uow, session, at)
                offline_ids = [
                    agent.id for agent in details.agents if agent.status != AgentStatus.ONLINE
                ]
                if offline_ids:
                    uow.commit()
                else:
                    policy_blocked = [
                        f"{agent.id} ({agent.policy_status})"
                        for agent in details.agents
                        if agent.policy_status != "APPLIED"
                    ]
                    if policy_blocked:
                        uow.commit()
                    else:
                        self._transition(uow, session, data, at)
                        uow.commit()
                        return details
            else:
                details = _snapshot_details(uow, session)
                self._transition(uow, session, data, at)
                uow.commit()
                return details

        if offline_ids:
            raise ReadinessGateError(
                f"offline agents block readiness: {', '.join(offline_ids)}"
            )
        raise ReadinessGateError(
            f"policy must be APPLIED before readiness: {', '.join(policy_blocked)}"
        )

    @staticmethod
    def _transition(
        uow: UnitOfWork,
        session: ExamSession,
        data: UpdateExamSessionStatusInput,
        at: datetime,
    ) -> None:
        session.transition_management(data.status, at)
        uow.sessions.save(session)
        if data.status == SessionState.FINISHED and session.policy is not None:
            uow.commands.add_many(
                [
                    Command(
                        session_id=session.id,
                        target_id=agent_id,
                        type=CommandType.RESTORE_BASELINE,
                        payload={
                            "format": "eecp-policy/v1",
                            "policy_hash": session.policy.policy_hash,
                            "baseline": "NORMAL",
                        },
                        created_at=at,
                    )
                    for agent_id in sorted(session.workstations)
                ]
            )
        uow.audits.append(
            session.id,
            actor=data.actor,
            action="SESSION_STATUS_UPDATED",
            target=session.id,
            details={"state": session.state},
        )


def _details(uow: UnitOfWork, session: ExamSession, at: datetime) -> ExamSessionDetails:
    return _build_details(uow, session, refresh_at=at)


def _snapshot_details(uow: UnitOfWork, session: ExamSession) -> ExamSessionDetails:
    return _build_details(uow, session, refresh_at=None)


def _build_details(
    uow: UnitOfWork,
    session: ExamSession,
    refresh_at: datetime | None,
) -> ExamSessionDetails:
    assignments = _assignment_targets(uow, session)
    agents = []
    for agent_id, assigned_at in assignments:
        agent = uow.agents.find(agent_id)
        if agent is None:
            agents.append(
                AssignedAgentDetails(
                    id=agent_id,
                    hostname=None,
                    ip_address=None,
                    status=None,
                    last_seen=None,
                    assigned_at=assigned_at,
                    policy_status=_policy_status(session, agent_id),
                )
            )
            continue
        if refresh_at is not None and agent.refresh_liveness(refresh_at):
            uow.agents.save(agent)
        agents.append(_agent_details(session, agent, assigned_at))
    violations = [
        PolicyViolationDetails(
            workstation_id=event.workstation_id,
            destination=event.destination,
            category=event.category,
            occurred_at=event.occurred_at,
        )
        for event in reversed(uow.telemetry.list_for_session(session.id))
        if event.event_type == "POLICY_VIOLATION" and event.action == "BLOCKED"
    ][:10]
    return ExamSessionDetails(session=session, agents=agents, violations=violations)


def _assignment_targets(uow: UnitOfWork, session: ExamSession) -> list[tuple[str, datetime | None]]:
    assignments = uow.session_workstations.list_for_session(session.id)
    if assignments or session.gateway_id is None:
        return [(assignment.agent_id, assignment.assigned_at) for assignment in assignments]
    return [(agent_id, None) for agent_id in sorted(session.workstations)]


def _agent_details(
    session: ExamSession, agent: Agent, assigned_at: datetime | None
) -> AssignedAgentDetails:
    return AssignedAgentDetails(
        id=agent.id,
        hostname=agent.hostname,
        ip_address=agent.ip_address,
        status=agent.status,
        last_seen=agent.last_seen,
        assigned_at=assigned_at,
        policy_status=_policy_status(session, agent.id),
    )


def _policy_status(session: ExamSession, agent_id: str) -> str:
    workstation = session.workstations[agent_id]
    if workstation.restored and session.state == SessionState.FINISHED:
        return "RESTORED"
    if workstation.policy_compliant:
        return "APPLIED"
    if workstation.readiness == Readiness.FAILED:
        return "FAILED"
    if workstation.desired_policy_hash:
        return "PENDING"
    return "NOT_ASSIGNED"


def _active_assignment_conflicts(
    uow: UnitOfWork, selected_ids: set[str]
) -> dict[str, str]:
    conflicts: dict[str, str] = {}
    terminal_states = {SessionState.FINISHED, SessionState.NORMAL}
    for existing in uow.sessions.list_all():
        if existing.state in terminal_states:
            continue
        for agent_id in selected_ids.intersection(existing.workstations):
            workstation = existing.workstations[agent_id]
            if workstation.readiness != Readiness.FAILED:
                conflicts[agent_id] = existing.id
    return conflicts
