from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from typing import Any

from app.domain.entities.operations import Incident, TelemetryEvent
from app.domain.value_objects.enums import Severity
from app.domain.value_objects.primitives import canonical_json


class IncidentPolicy:
    """Pure incident rules for explicit violations and correlated infrastructure faults."""

    DNS_THRESHOLD = 3

    def evaluate(
        self,
        current: TelemetryEvent,
        session_events: Sequence[TelemetryEvent],
        open_incident_categories: set[str],
    ) -> Incident | None:
        if current.event_type == "POLICY_VIOLATION" and current.action == "BLOCKED":
            return Incident(
                session_id=current.session_id,
                workstation_id=current.workstation_id,
                category="POLICY_VIOLATION",
                severity=Severity.WARNING,
                evidence={
                    "event_ids": [current.id],
                    "destination": current.destination,
                    "action": current.action,
                },
            )

        if current.severity == Severity.CRITICAL:
            category = f"CRITICAL_{current.category}"
            if category not in open_incident_categories:
                return Incident(
                    session_id=current.session_id,
                    workstation_id=current.workstation_id,
                    category=category,
                    severity=Severity.CRITICAL,
                    evidence={"event_ids": [current.id]},
                )

        if current.event_type == "DNS_FAILURE":
            dns_events = [event for event in session_events if event.event_type == "DNS_FAILURE"]
            affected = sorted({event.workstation_id for event in dns_events})
            category = "INFRASTRUCTURE_DNS"
            if (
                len(affected) >= self.DNS_THRESHOLD
                and category not in open_incident_categories
            ):
                return Incident(
                    session_id=current.session_id,
                    category=category,
                    severity=Severity.WARNING,
                    evidence={
                        "affected_workstations": affected,
                        "event_ids": [event.id for event in dns_events],
                        "reason": "correlated DNS failures across multiple workstations",
                    },
                )
        return None


def compute_audit_hash(previous_hash: str, payload: dict[str, Any]) -> str:
    return sha256(f"{previous_hash}|{canonical_json(payload)}".encode()).hexdigest()
