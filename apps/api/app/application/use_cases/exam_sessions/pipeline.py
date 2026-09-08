from __future__ import annotations

from datetime import UTC, datetime

from app.application.dtos.exam_pipeline import (
    CreateSessionInput,
    DeployPolicyInput,
    StartSessionInput,
    SubmitPreflightInput,
    TelemetryInput,
)
from app.domain.entities.exam_session import ExamSession
from app.domain.entities.operations import Command, TelemetryEvent
from app.domain.exceptions.errors import InvalidStateTransitionError, PolicyValidationError
from app.domain.interfaces.unit_of_work import UnitOfWorkFactory
from app.domain.services.policies import IncidentPolicy
from app.domain.value_objects.enums import (
    CommandType,
    IncidentStatus,
    SessionState,
)


class ExamPipelineService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        incident_policy: IncidentPolicy | None = None,
    ):
        self._uow_factory = uow_factory
        self._incident_policy = incident_policy or IncidentPolicy()

    def create_session(self, data: CreateSessionInput) -> ExamSession:
        session = ExamSession.create(
            exam_name=data.exam_name,
            room_id=data.room_id,
            gateway_id=data.gateway_id,
            workstation_ids=data.workstation_ids,
        )
        with self._uow_factory() as uow:
            uow.sessions.add(session)
            uow.audits.append(
                session.id,
                actor=data.actor,
                action="SESSION_CREATED",
                target=session.id,
                details={
                    "room_id": session.room_id,
                    "gateway_id": session.gateway_id,
                    "workstations": sorted(session.workstations),
                },
            )
            uow.commit()
        return session

    def deploy_policy(self, data: DeployPolicyInput) -> ExamSession:
        with self._uow_factory() as uow:
            session = uow.sessions.get(data.session_id)
            policy = session.deploy_policy(data.profile, data.rules)
            targets = [session.gateway_id, *sorted(session.workstations)]
            commands = [
                Command(
                    session_id=session.id,
                    target_id=target,
                    type=CommandType.APPLY_POLICY,
                    payload={
                        "policy_hash": policy.policy_hash,
                        "version": policy.version,
                        "profile": policy.profile,
                        "rules": policy.rules,
                    },
                )
                for target in targets
            ]
            uow.sessions.save(session)
            uow.commands.add_many(commands)
            uow.audits.append(
                session.id,
                actor=data.actor,
                action="POLICY_DEPLOYED",
                target=session.id,
                details={
                    "policy_version": policy.version,
                    "policy_hash": policy.policy_hash,
                    "targets": targets,
                },
            )
            uow.commit()
            return session

    def submit_preflight(self, data: SubmitPreflightInput) -> ExamSession:
        with self._uow_factory() as uow:
            session = uow.sessions.get(data.session_id)
            readiness = session.record_preflight(data.workstation_id, data.checks)
            uow.sessions.save(session)
            uow.audits.append(
                session.id,
                actor=data.actor,
                action="PREFLIGHT_REPORTED",
                target=data.workstation_id,
                details={
                    "readiness": readiness,
                    "failed_checks": [check.name for check in data.checks if not check.passed],
                },
            )
            uow.commit()
            return session

    def start_session(self, data: StartSessionInput) -> ExamSession:
        with self._uow_factory() as uow:
            session = uow.sessions.get(data.session_id)
            session.start(data.force, data.reason, datetime.now(UTC))
            uow.sessions.save(session)
            uow.audits.append(
                session.id,
                actor=data.actor,
                action="SESSION_FORCE_STARTED" if data.force else "SESSION_STARTED",
                target=session.id,
                details={"reason": data.reason, "state": session.state},
            )
            uow.commit()
            return session

    def ingest_telemetry(self, data: TelemetryInput) -> tuple[TelemetryEvent, str | None]:
        event = TelemetryEvent(
            session_id=data.session_id,
            workstation_id=data.workstation_id,
            event_type=data.event_type,
            severity=data.severity,
            category=data.category,
            action=data.action,
            destination=data.destination,
            correlation_id=data.correlation_id,
            payload=data.payload,
        )
        with self._uow_factory() as uow:
            session = uow.sessions.get(data.session_id)
            if session.state != SessionState.RUNNING:
                raise InvalidStateTransitionError("telemetry is accepted only for RUNNING sessions")
            if data.workstation_id not in session.workstations:
                raise PolicyValidationError("telemetry workstation does not belong to session")
            uow.telemetry.add(event)
            session_events = uow.telemetry.list_for_session(session.id)
            current_incidents = uow.incidents.list_for_session(session.id)
            open_categories = {
                item.category for item in current_incidents if item.status == IncidentStatus.OPEN
            }
            incident = self._incident_policy.evaluate(event, session_events, open_categories)
            if incident:
                uow.incidents.add(incident)
                uow.audits.append(
                    session.id,
                    actor="incident-engine",
                    action="INCIDENT_CREATED",
                    target=incident.id,
                    details={"category": incident.category, "evidence": incident.evidence},
                )
            uow.commit()
            return event, incident.id if incident else None

    def finish_session(self, session_id: str, actor: str) -> ExamSession:
        with self._uow_factory() as uow:
            session = uow.sessions.get(session_id)
            session.finish(datetime.now(UTC))
            targets = [session.gateway_id, *sorted(session.workstations)]
            commands = [
                Command(
                    session_id=session.id,
                    target_id=target,
                    type=CommandType.RESTORE_BASELINE,
                    payload={"baseline": "NORMAL"},
                )
                for target in targets
            ]
            uow.sessions.save(session)
            uow.commands.add_many(commands)
            uow.audits.append(
                session.id,
                actor=actor,
                action="SESSION_FINISHED",
                target=session.id,
                details={"restore_targets": targets},
            )
            uow.commit()
            return session

    def get_session(self, session_id: str) -> ExamSession:
        with self._uow_factory() as uow:
            return uow.sessions.get(session_id)

    def get_summary(self, session_id: str) -> dict:
        with self._uow_factory() as uow:
            session = uow.sessions.get(session_id)
            telemetry = uow.telemetry.list_for_session(session_id)
            incidents = uow.incidents.list_for_session(session_id)
            audits = uow.audits.list_for_session(session_id)
            readiness = {}
            for workstation in session.workstations.values():
                readiness[workstation.readiness.value] = (
                    readiness.get(workstation.readiness.value, 0) + 1
                )
            return {
                "session": session.to_dict(),
                "readiness_counts": readiness,
                "telemetry_count": len(telemetry),
                "blocked_event_count": sum(event.action == "BLOCKED" for event in telemetry),
                "incidents": [
                    {
                        "id": item.id,
                        "category": item.category,
                        "severity": item.severity,
                        "status": item.status,
                        "evidence": item.evidence,
                    }
                    for item in incidents
                ],
                "audit_event_count": len(audits),
                "audit_chain_valid": uow.audits.verify_chain(session_id),
            }
