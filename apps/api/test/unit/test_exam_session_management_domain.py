from datetime import UTC, datetime, timedelta

import pytest
from app.domain.entities.exam_session import ExamSession
from app.domain.entities.session_workstation import SessionWorkstation
from app.domain.exceptions.errors import InvalidStateTransitionError, PolicyValidationError
from app.domain.value_objects.enums import SessionState

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


def test_create_managed_session_has_no_gateway_and_tracks_updated_at() -> None:
    session = ExamSession.create_managed(
        " PBL4 Final ", " A101 ", [" PC01 ", "PC02"], NOW
    )

    assert session.exam_name == "PBL4 Final"
    assert session.room_id == "A101"
    assert session.gateway_id is None
    assert session.state == SessionState.CREATED
    assert sorted(session.workstations) == ["PC01", "PC02"]
    assert session.created_at == NOW
    assert session.updated_at == NOW


def test_session_workstation_assignment_contains_only_relation_data() -> None:
    assignment = SessionWorkstation.assign("ses_1", " PC01 ", NOW)

    assert assignment.id.startswith("sws_")
    assert assignment.session_id == "ses_1"
    assert assignment.agent_id == "PC01"
    assert assignment.assigned_at == NOW


def test_managed_creation_rejects_duplicate_agent_ids() -> None:
    with pytest.raises(PolicyValidationError, match="unique"):
        ExamSession.create_managed("Exam", "A101", ["PC01", " PC01 "], NOW)


@pytest.mark.parametrize("blank_agent_id", ["", "  "])
def test_managed_creation_rejects_blank_agent_ids(blank_agent_id: str) -> None:
    with pytest.raises(PolicyValidationError, match="empty"):
        ExamSession.create_managed("Exam", "A101", ["PC01", blank_agent_id], NOW)


def test_management_lifecycle_reaches_finished_in_order() -> None:
    session = ExamSession.create_managed("Exam", "A101", ["PC01"], NOW)

    session.transition_management(SessionState.READY, NOW + timedelta(seconds=1))
    session.transition_management(SessionState.RUNNING, NOW + timedelta(seconds=2))
    session.transition_management(SessionState.FINISHED, NOW + timedelta(seconds=3))

    assert session.state == SessionState.FINISHED
    assert session.started_at == NOW + timedelta(seconds=2)
    assert session.finished_at == NOW + timedelta(seconds=3)
    assert session.updated_at == NOW + timedelta(seconds=3)


def test_management_lifecycle_rejects_skipped_transition() -> None:
    session = ExamSession.create_managed("Exam", "A101", ["PC01"], NOW)

    with pytest.raises(InvalidStateTransitionError, match="CREATED"):
        session.transition_management(SessionState.RUNNING, NOW)


def test_management_lifecycle_allows_finishing_from_created_or_ready() -> None:
    session = ExamSession.create_managed("Exam", "A101", ["PC01"], NOW)
    session.transition_management(SessionState.FINISHED, NOW + timedelta(seconds=1))
    assert session.state == SessionState.FINISHED

    session2 = ExamSession.create_managed("Exam2", "A101", ["PC01"], NOW)
    session2.transition_management(SessionState.READY, NOW + timedelta(seconds=1))
    session2.transition_management(SessionState.FINISHED, NOW + timedelta(seconds=2))
    assert session2.state == SessionState.FINISHED


def test_pipeline_session_rejects_management_transition() -> None:
    session = ExamSession.create("Exam", "A101", "gw-a101", ["PC01"])

    with pytest.raises(InvalidStateTransitionError, match="pipeline"):
        session.transition_management(SessionState.READY, NOW)


def test_management_session_rejects_policy_deployment() -> None:
    session = ExamSession.create_managed("Exam", "A101", ["PC01"], NOW)

    with pytest.raises(InvalidStateTransitionError, match="gateway"):
        session.deploy_policy("PROGRAMMING_EXAM", {})


def test_management_session_rejects_pipeline_start() -> None:
    session = ExamSession.create_managed("Exam", "A101", ["PC01"], NOW)
    session.transition_management(SessionState.READY, NOW)

    with pytest.raises(InvalidStateTransitionError, match="gateway"):
        session.start(force=False, reason=None, at=NOW)


def test_management_session_rejects_pipeline_finish() -> None:
    session = ExamSession.create_managed("Exam", "A101", ["PC01"], NOW)
    session.transition_management(SessionState.READY, NOW)
    session.transition_management(SessionState.RUNNING, NOW)

    with pytest.raises(InvalidStateTransitionError, match="gateway"):
        session.finish(NOW)

    assert session.state == SessionState.RUNNING


def test_session_serialization_round_trips_updated_at() -> None:
    session = ExamSession.create_managed("Exam", "A101", ["PC01"], NOW)
    updated_at = NOW + timedelta(seconds=1)
    session.transition_management(SessionState.READY, updated_at)

    serialized = session.to_dict()

    assert serialized["updated_at"] == updated_at.isoformat()
    assert ExamSession.from_dict(serialized).updated_at == updated_at


def test_session_deserialization_defaults_updated_at_to_created_at() -> None:
    session = ExamSession.create_managed("Exam", "A101", ["PC01"], NOW)
    serialized = session.to_dict()
    serialized.pop("updated_at")

    restored = ExamSession.from_dict(serialized)

    assert restored.updated_at == NOW


@pytest.mark.parametrize("gateway_id", ["", "  "])
def test_session_deserialization_rejects_blank_gateway_id(gateway_id: str) -> None:
    session = ExamSession.create("Exam", "A101", "gw-a101", ["PC01"])
    serialized = session.to_dict()
    serialized["gateway_id"] = gateway_id

    with pytest.raises(PolicyValidationError, match="gateway"):
        ExamSession.from_dict(serialized)
