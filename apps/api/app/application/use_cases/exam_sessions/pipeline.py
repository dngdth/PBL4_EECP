from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.application.dtos.exam_pipeline import (
    CreateSessionInput,
    DeployPolicyInput,
    RequestPreflightInput,
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
from app.domain.value_objects.primitives import new_id


class ExamPipelineService:
    LATE_SECURITY_EVENT_GRACE = timedelta(hours=24)
    LATE_SECURITY_EVENT_TYPES = frozenset(
        {
            "USER_SESSION_CHANGED",
            "FORBIDDEN_PROCESS_DETECTED",
            "ENFORCEMENT_ACTION",
            "ENFORCEMENT_TAMPER",
            "SERVICE_HEALTH",
            "DEVICE_EVENT",
            "POLICY_INTEGRITY",
        }
    )
    PRIVILEGED_EVENT_SOURCES = frozenset(
        {"privileged-service", "windows-service-session-notification"}
    )

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        incident_policy: IncidentPolicy | None = None,
        *,
        clock=lambda: datetime.now(UTC),
    ):
        self._uow_factory = uow_factory
        self._incident_policy = incident_policy or IncidentPolicy()
        self._clock = clock

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

    def request_preflight(self, data: RequestPreflightInput) -> Command:
        """Queue a real Service HEALTH_CHECK for a session workstation."""

        with self._uow_factory() as uow:
            session = uow.sessions.get(data.session_id)
            if data.workstation_id not in session.workstations:
                raise PolicyValidationError(
                    "preflight workstation does not belong to session"
                )
            command = Command(
                session_id=session.id,
                target_id=data.workstation_id,
                type=CommandType.HEALTH_CHECK,
                payload={"reason": "PREFLIGHT"},
            )
            uow.commands.add_many([command])
            uow.audits.append(
                session.id,
                actor=data.actor,
                action="PREFLIGHT_HEALTH_CHECK_REQUESTED",
                target=data.workstation_id,
                details={"command_id": command.id},
            )
            uow.commit()
            return command

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
        event, incident_id, _duplicate = self.ingest_protocol_event(data)
        return event, incident_id

    def list_security_events(
        self,
        session_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        agent_id: str | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> tuple[list[TelemetryEvent], int]:
        with self._uow_factory() as uow:
            uow.sessions.get(session_id)
            events = uow.telemetry.list_for_session(session_id)
        filtered = [
            event
            for event in events
            if (agent_id is None or event.workstation_id == agent_id)
            and (event_type is None or event.event_type == event_type)
            and (severity is None or event.severity.value == severity)
            and (occurred_from is None or event.occurred_at >= occurred_from)
            and (occurred_to is None or event.occurred_at <= occurred_to)
        ]
        filtered.sort(key=lambda event: (event.occurred_at, event.id), reverse=True)
        return filtered[offset : offset + limit], len(filtered)

    def ingest_protocol_event(
        self, data: TelemetryInput
    ) -> tuple[TelemetryEvent, str | None, bool]:
        encoded_payload = json.dumps(data.payload, separators=(",", ":")).encode()
        if len(encoded_payload) > 64 * 1024:
            raise PolicyValidationError("telemetry payload exceeds 64 KiB")
        if data.occurred_at is not None:
            if data.occurred_at.tzinfo is None:
                raise PolicyValidationError("telemetry timestamp must be timezone-aware")
            if data.occurred_at > self._clock() + timedelta(minutes=5):
                raise PolicyValidationError("telemetry timestamp is too far in the future")
        event = TelemetryEvent(
            id=data.event_id or new_id("evt"),
            session_id=data.session_id,
            workstation_id=data.workstation_id,
            event_type=data.event_type,
            severity=data.severity,
            category=data.category,
            action=data.action,
            destination=data.destination,
            correlation_id=data.correlation_id,
            payload=data.payload,
            occurred_at=data.occurred_at or self._clock(),
        )
        with self._uow_factory() as uow:
            if data.event_id:
                existing = uow.telemetry.find(data.event_id)
                if existing is not None:
                    if not _same_event_content(
                        existing, event, check_occurred_at=data.occurred_at is not None
                    ):
                        raise PolicyValidationError(
                            "event_id was already used for different telemetry content"
                        )
                    return existing, None, True
            session = uow.sessions.get(data.session_id)
            if data.workstation_id not in session.workstations:
                raise PolicyValidationError("telemetry workstation does not belong to session")
            if session.state != SessionState.RUNNING:
                self._validate_late_security_event(session, event)
                event = replace(event, payload={**event.payload, "late_arrival": True})
                uow.telemetry.add(event)
                uow.audits.append(
                    session.id,
                    actor=event.workstation_id,
                    action="LATE_SECURITY_EVENT_ACCEPTED",
                    target=event.id,
                    details={
                        "event_type": event.event_type,
                        "occurred_at": event.occurred_at.isoformat(),
                        "received_at": self._clock().isoformat(),
                    },
                )
                uow.commit()
                return event, None, False
            uow.telemetry.add(event)
            session_events = uow.telemetry.list_for_session(session.id)
            current_incidents = uow.incidents.list_for_session(session.id)
            open_categories = {
                (item.workstation_id, item.category)
                for item in current_incidents
                if item.status == IncidentStatus.OPEN
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
            return event, incident.id if incident else None, False

    def _validate_late_security_event(
        self, session: ExamSession, event: TelemetryEvent
    ) -> None:
        if session.state not in {
            SessionState.FINISHED,
            SessionState.RESTORING,
            SessionState.NORMAL,
        }:
            raise InvalidStateTransitionError(
                "telemetry is accepted only for RUNNING sessions"
            )
        if event.event_type not in self.LATE_SECURITY_EVENT_TYPES:
            raise InvalidStateTransitionError(
                "event type is not eligible for late security ingestion"
            )
        if event.payload.get("source") not in self.PRIVILEGED_EVENT_SOURCES:
            raise PolicyValidationError(
                "late security event lacks privileged Service provenance"
            )
        if not event.id or session.started_at is None or session.finished_at is None:
            raise PolicyValidationError("late security event lacks required timing evidence")
        if not session.started_at <= event.occurred_at <= session.finished_at:
            raise PolicyValidationError(
                "late security event did not occur during the RUNNING interval"
            )
        if self._clock() > session.finished_at + self.LATE_SECURITY_EVENT_GRACE:
            raise PolicyValidationError("late security event exceeded the delivery grace period")

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
                        "workstation_id": item.workstation_id,
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


def _same_event_content(
    existing: TelemetryEvent,
    incoming: TelemetryEvent,
    check_occurred_at: bool = True,
) -> bool:
    existing_payload = dict(existing.payload)
    existing_payload.pop("late_arrival", None)
    incoming_payload = dict(incoming.payload)
    incoming_payload.pop("late_arrival", None)
    return (
        existing.session_id == incoming.session_id
        and existing.workstation_id == incoming.workstation_id
        and existing.event_type == incoming.event_type
        and existing.severity == incoming.severity
        and existing.category == incoming.category
        and existing.action == incoming.action
        and existing.destination == incoming.destination
        and existing.correlation_id == incoming.correlation_id
        and (not check_occurred_at or existing.occurred_at == incoming.occurred_at)
        and existing_payload == incoming_payload
    )
