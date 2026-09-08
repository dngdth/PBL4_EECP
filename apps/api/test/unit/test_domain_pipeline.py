import pytest
from app.domain.entities.exam_session import ExamSession, PreflightCheck
from app.domain.exceptions.errors import PolicyValidationError, ReadinessGateError
from app.domain.value_objects.enums import Readiness, SessionState


def test_policy_rejects_application_allow_deny_overlap() -> None:
    session = ExamSession.create("Programming C", "F301", "gw-f301", ["PC01"])

    with pytest.raises(PolicyValidationError, match="both allowed and denied"):
        session.deploy_policy(
            "INTERNET_NO_AI",
            {"applications": {"allow": ["vscode.exe"], "deny": ["vscode.exe"]}},
        )


def test_session_cannot_start_before_readiness_gate() -> None:
    session = ExamSession.create("Programming C", "F301", "gw-f301", ["PC01"])

    with pytest.raises(ReadinessGateError, match="READY is required"):
        session.start(force=False, reason=None, at=session.created_at)


def test_noncritical_failure_results_in_warning_but_session_is_ready() -> None:
    session = ExamSession.create("Programming C", "F301", "gw-f301", ["PC01"])
    policy = session.deploy_policy("PROGRAMMING_EXAM", {})
    session.acknowledge_policy("gw-f301", policy.policy_hash)
    session.acknowledge_policy("PC01", policy.policy_hash)

    readiness = session.record_preflight(
        "PC01",
        [
            PreflightCheck("agent_online", True, critical=True),
            PreflightCheck("ide_minor_version", False, critical=False),
        ],
    )

    assert readiness == Readiness.WARNING
    assert session.state == SessionState.READY


def test_multiple_policy_delivery_failures_remain_degraded() -> None:
    session = ExamSession.create("Programming C", "F301", "gw-f301", ["PC01"])
    session.deploy_policy("PROGRAMMING_EXAM", {})

    session.record_policy_failure("gw-f301")
    session.record_policy_failure("PC01")

    assert session.state == SessionState.DEGRADED
    assert session.workstations["PC01"].readiness == Readiness.FAILED
