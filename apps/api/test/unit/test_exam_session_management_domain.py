from datetime import UTC, datetime, timedelta

import pytest
from app.domain.entities.exam_session import ExamSession, PolicyDocument
from app.domain.entities.session_workstation import SessionWorkstation
from app.domain.exceptions.errors import InvalidStateTransitionError, PolicyValidationError
from app.domain.value_objects.enums import SessionState
from contracts.v2.policy import PolicyRules, compute_policy_hash

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


@pytest.mark.parametrize(
    "invalid_rules, expected_match",
    [
        (
            {
                "applications": {"allow": ["exam.exe"]},
                "network": {"strict_mode": True},
            },
            "extra_forbidden",
        ),
        (
            {
                "applications": {"allow": ["exam.exe"]},
                "network": {"network_lockdown": True},
            },
            "extra_forbidden",
        ),
        (
            {
                "applications": {"allow": ["exam.exe"]},
                "usb_storage_blocked": True,
            },
            "extra_forbidden",
        ),
        (
            {
                "devices": {"usb": "invalid_access"},
            },
            "Input should be 'allow' or 'deny'",
        ),
        (
            {
                "devices": {"usb": "deny", "usb_storage": "deny"},
            },
            "use devices.usb or legacy usb_storage, not both",
        ),
        (
            {
                "network": {"block": ["generative_ai"], "blocked_categories": ["vpn_proxy"]},
            },
            "use network.block or legacy blocked_categories, not both",
        ),
        (
            {
                "network": {"blocked_ips": ["999.999.999.999"]},
            },
            "invalid blocked IP address",
        ),
        (
            {
                "applications": {"allow": ["app.exe"], "deny": ["app.exe"]},
            },
            "applications cannot be both allowed and denied",
        ),
    ],
)
def test_policy_document_rejects_incompatible_protocol_v2_rules(
    invalid_rules: dict, expected_match: str
) -> None:
    with pytest.raises(PolicyValidationError, match=expected_match):
        PolicyDocument.create("STANDARD", invalid_rules, 1)


def test_policy_document_accepts_valid_protocol_v2_rules() -> None:
    valid_rules = {
        "applications": {"allow": ["exam-browser.exe", "calc.exe"], "deny": ["cheat.exe"]},
        "network": {
            "block": ["generative_ai", "social_network"],
            "allow_domains": ["exam.example.edu"],
        },
        "devices": {"usb": "deny"},
    }

    doc = PolicyDocument.create("STANDARD_EXAM", valid_rules, 1)
    expected_hash = compute_policy_hash(
        "STANDARD_EXAM", 1, PolicyRules.model_validate(valid_rules)
    )

    assert doc.profile == "STANDARD_EXAM"
    assert doc.version == 1
    assert doc.policy_hash == expected_hash
    assert doc.rules["devices"]["usb"] == "deny"
    assert doc.rules["applications"]["allow"] == ["exam-browser.exe", "calc.exe"]


def test_policy_document_and_session_support_legacy_rule_vocabulary_and_preserve_hash() -> None:
    from agent.domain.policy import parse_policy_payload

    legacy_rules = {
        "applications": {"deny": ["cheat.exe"]},
        "network": {"blocked_categories": ["generative_ai"]},
        "devices": {"usb_storage": "deny"},
    }

    doc = PolicyDocument.create("LEGACY_PROFILE", legacy_rules, 1)
    expected_hash = compute_policy_hash(
        "LEGACY_PROFILE", 1, PolicyRules.model_validate(legacy_rules)
    )
    assert doc.policy_hash == expected_hash

    # Verify Agent parses legacy vocabulary
    agent_spec = parse_policy_payload({
        "policy_hash": doc.policy_hash,
        "version": doc.version,
        "profile": doc.profile,
        "rules": doc.rules,
    })
    assert agent_spec.policy_hash == expected_hash
    assert agent_spec.usb_deny is True
    assert agent_spec.blocked_categories == ("generative_ai",)
    assert agent_spec.denied_applications == ("cheat.exe",)

    # Verify existing session reconstitution preserves the hash without mutation
    session = ExamSession.create_managed("LegacyExam", "R1", ["PC01"], NOW)
    session.policy = doc
    serialized = session.to_dict()
    assert serialized["policy"]["policy_hash"] == expected_hash

    restored = ExamSession.from_dict(serialized)
    assert restored.policy is not None
    assert restored.policy.policy_hash == expected_hash
