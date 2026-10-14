import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from app.domain.entities.exam_session import PolicyDocument
from pydantic import ValidationError

from contracts.v2 import (
    ERROR_DESCRIPTIONS,
    Ack,
    AckStatus,
    ApplyPolicyPayload,
    Command,
    CommandType,
    ErrorCode,
    Event,
    EventReceipt,
    EventReceiptStatus,
    HealthCheckPayload,
    PolicyEnvelope,
    PolicyRules,
    Presence,
    RestoreBaselinePayload,
    ServiceRequest,
    ServiceResult,
    compute_policy_hash,
)

NOW = datetime(2026, 9, 28, 10, 20, 31, tzinfo=UTC)
HASH = "ae93cd20cf6d57da96425807a77f4ddf6570f746a3a4a361f05fd8992787045c"
RULES = {
    "applications": {"allow": ["vscode.exe"], "deny": ["anydesk.exe"]},
    "network": {"block": ["generative_ai"]},
    "devices": {"usb": "deny"},
}
EXAMPLES = Path(__file__).resolve().parents[4] / "contracts" / "v2" / "examples"


def _policy(**changes) -> PolicyEnvelope:
    values = {
        "protocol_version": 2,
        "policy_id": "INTERNET_NO_AI",
        "policy_version": 1,
        "policy_hash": HASH,
        "session_id": "ses-1",
        "issued_at": NOW,
        "expires_at": NOW + timedelta(hours=2),
        "rules": RULES,
        "signature": None,
    }
    values.update(changes)
    return PolicyEnvelope.model_validate(values)


def _command(command_type: CommandType) -> Command:
    payload = {
        CommandType.APPLY_POLICY: ApplyPolicyPayload(policy=_policy()),
        CommandType.RESTORE_BASELINE: RestoreBaselinePayload(baseline="NORMAL"),
        CommandType.HEALTH_CHECK: HealthCheckPayload(nonce="health-1"),
    }[command_type]
    return Command(
        protocol_version=2,
        command_id=f"cmd-{command_type.value.lower()}",
        command_type=command_type,
        target_id="PC01",
        session_id="ses-1",
        issued_at=NOW,
        deadline=NOW + timedelta(minutes=1),
        policy_hash=HASH if command_type != CommandType.HEALTH_CHECK else None,
        payload=payload,
        correlation_id="corr-1",
    )


def test_valid_policy_envelope_and_json_round_trip() -> None:
    policy = _policy()

    encoded = policy.to_json()

    assert PolicyEnvelope.model_validate_json(encoded) == policy
    assert json.loads(encoded)["issued_at"] == "2026-09-28T10:20:31Z"
    assert policy.policy_hash == HASH


@pytest.mark.parametrize("field", ["policy_id", "session_id", "policy_hash"])
def test_policy_rejects_missing_required_identity(field: str) -> None:
    payload = _policy().model_dump()
    payload.pop(field)

    with pytest.raises(ValidationError):
        PolicyEnvelope.model_validate(payload)


def test_policy_rejects_unsupported_version_and_invalid_expiry() -> None:
    with pytest.raises(ValidationError):
        _policy(protocol_version=3)
    with pytest.raises(ValidationError, match="expires_at"):
        _policy(expires_at=NOW)


def test_policy_rejects_invalid_rules_and_hash_mismatch() -> None:
    with pytest.raises(ValidationError):
        _policy(rules={"network": {"arbitrary": ["example.com"]}})
    with pytest.raises(ValidationError, match="policy_hash"):
        _policy(rules={"devices": {"usb": "allow"}})


def test_contracts_reject_unknown_fields() -> None:
    payload = _policy().model_dump()
    payload["transport"] = "websocket"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PolicyEnvelope.model_validate(payload)


def test_current_policy_maps_without_changing_hash_semantics() -> None:
    legacy = PolicyDocument.create("internet_no_ai", RULES, 1)
    mapped = PolicyEnvelope.from_legacy(
        policy_id=legacy.profile,
        policy_version=legacy.version,
        policy_hash=legacy.policy_hash,
        session_id="ses-1",
        issued_at=NOW,
        rules=legacy.rules,
    )

    assert mapped.policy_hash == legacy.policy_hash == HASH


def test_legacy_pipeline_policy_vocabulary_maps_without_hash_change() -> None:
    rules = {
        "applications": {
            "allow": ["vscode.exe", "gcc.exe"],
            "deny": ["anydesk.exe", "teamviewer.exe"],
        },
        "network": {
            "allow_domains": ["lms.dut.udn.vn", "cppreference.com"],
            "blocked_categories": ["generative_ai", "vpn_proxy"],
        },
        "devices": {"usb_storage": "deny"},
    }
    legacy = PolicyDocument.create("PROGRAMMING_EXAM", rules, 1)

    mapped = PolicyEnvelope.from_legacy(
        policy_id=legacy.profile,
        policy_version=legacy.version,
        policy_hash=legacy.policy_hash,
        session_id="ses-legacy",
        issued_at=NOW,
        rules=legacy.rules,
    )

    assert mapped.policy_hash == legacy.policy_hash


def test_policy_hash_is_stable_for_key_order_and_unicode() -> None:
    first = PolicyRules.model_validate(
        {"applications": {"deny": ["ứng-dụng.exe"], "allow": []}}
    )
    second = PolicyRules.model_validate(
        {"applications": {"allow": [], "deny": ["ứng-dụng.exe"]}}
    )

    assert compute_policy_hash("UNICODE", 1, first) == compute_policy_hash(
        "UNICODE", 1, second
    )


def test_optional_firewall_fields_validate_without_changing_legacy_hash() -> None:
    legacy = PolicyRules.model_validate(RULES)
    assert compute_policy_hash("INTERNET_NO_AI", 1, legacy) == HASH

    firewall_rules = PolicyRules.model_validate(
        {
            "network": {
                "blocked_domains": ["example.com"],
                "blocked_ips": ["203.0.113.10", "2001:db8::10"],
                "blocked_cidrs": ["198.51.100.0/24", "2001:db8:1::/64"],
            }
        }
    )
    assert firewall_rules.network is not None
    assert firewall_rules.network.blocked_ips == (
        "203.0.113.10",
        "2001:db8::10",
    )


@pytest.mark.parametrize(
    "network",
    [
        {"blocked_ips": ["999.1.1.1"]},
        {"blocked_ips": ["203.0.113.10; powershell evil"]},
        {"blocked_cidrs": ["10.0.0.1/500"]},
        {"blocked_cidrs": ["198.51.100.1/24"]},
    ],
)
def test_policy_contract_rejects_invalid_firewall_addresses(network: dict) -> None:
    with pytest.raises(ValidationError):
        PolicyRules.model_validate({"network": network})


@pytest.mark.parametrize("command_type", list(CommandType))
def test_allowlisted_commands_validate_and_round_trip(command_type: CommandType) -> None:
    command = _command(command_type)

    assert Command.model_validate_json(command.to_json()) == command


def test_command_rejects_unknown_or_arbitrary_operation() -> None:
    payload = _command(CommandType.HEALTH_CHECK).model_dump(mode="json")
    for operation in ("RUN_SHELL", "POWERSHELL", "EXECUTE"):
        payload["command_type"] = operation
        with pytest.raises(ValidationError):
            Command.model_validate(payload)


def test_event_receipt_is_distinct_from_command_ack() -> None:
    receipt = EventReceipt(
        protocol_version=2,
        event_id="EVT-001",
        status=EventReceiptStatus.ACCEPTED,
        received_at=NOW,
    )

    assert receipt.status == EventReceiptStatus.ACCEPTED
    with pytest.raises(ValidationError, match="requires error_code"):
        EventReceipt(
            protocol_version=2,
            event_id="EVT-002",
            status=EventReceiptStatus.REJECTED,
            received_at=NOW,
        )


def test_command_requires_identity_and_valid_deadline() -> None:
    payload = _command(CommandType.HEALTH_CHECK).model_dump()
    payload.pop("command_id")
    with pytest.raises(ValidationError):
        Command.model_validate(payload)

    payload = _command(CommandType.HEALTH_CHECK).model_dump()
    payload["deadline"] = NOW - timedelta(seconds=1)
    with pytest.raises(ValidationError, match="deadline"):
        Command.model_validate(payload)


def test_apply_command_requires_matching_policy_and_session() -> None:
    payload = _command(CommandType.APPLY_POLICY).model_dump()
    payload["policy_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="policy_hash"):
        Command.model_validate(payload)

    payload = _command(CommandType.APPLY_POLICY).model_dump()
    payload["session_id"] = "ses-other"
    with pytest.raises(ValidationError, match="session_id"):
        Command.model_validate(payload)


def test_ack_success_failure_validation_and_round_trip() -> None:
    success = Ack(
        protocol_version=2,
        ack_id="ack-1",
        command_id="cmd-1",
        status=AckStatus.SUCCEEDED,
        applied_hash=HASH,
        service_version="1.1.0",
        occurred_at=NOW,
        correlation_id="corr-1",
    )
    failure = Ack(
        protocol_version=2,
        ack_id="ack-2",
        command_id="cmd-1",
        status=AckStatus.FAILED,
        service_version="1.1.0",
        error_code=ErrorCode.EXECUTION_FAILED,
        error_message="Access denied",
        occurred_at=NOW,
        correlation_id="corr-1",
    )

    assert Ack.model_validate_json(success.to_json()) == success
    assert Ack.model_validate_json(failure.to_json()) == failure


def test_ack_rejects_unknown_status_and_unstructured_failure() -> None:
    payload = json.loads((EXAMPLES / "ack-success.json").read_text(encoding="utf-8"))
    payload["status"] = "RECEIVED"
    with pytest.raises(ValidationError):
        Ack.model_validate(payload)

    payload["status"] = "FAILED"
    payload["error_code"] = None
    with pytest.raises(ValidationError, match="error_code"):
        Ack.model_validate(payload)


def test_event_requires_stable_id_and_round_trips_unicode_payload() -> None:
    payload = json.loads((EXAMPLES / "violation-event.json").read_text(encoding="utf-8"))
    event = Event.model_validate(payload)
    assert Event.model_validate_json(event.to_json()) == event
    assert event.payload["description"] == "Truy cập miền bị chặn"

    payload.pop("event_id")
    with pytest.raises(ValidationError):
        Event.model_validate(payload)


def test_presence_health_timestamp_and_round_trip() -> None:
    payload = json.loads((EXAMPLES / "presence.json").read_text(encoding="utf-8"))
    presence = Presence.model_validate(payload)
    assert Presence.model_validate_json(presence.to_json()) == presence

    payload["health"] = "UNKNOWN"
    with pytest.raises(ValidationError):
        Presence.model_validate(payload)
    payload["health"] = "ONLINE"
    payload["last_seen"] = "2026-09-28T17:20:31+07:00"
    with pytest.raises(ValidationError, match="UTC"):
        Presence.model_validate(payload)


def test_all_timestamps_reject_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _policy(issued_at=datetime(2026, 9, 28, 10, 20, 31))
    with pytest.raises(ValidationError, match="UTC"):
        _policy(issued_at=NOW.astimezone(timezone(timedelta(hours=7))))


def test_service_contract_is_allowlisted_and_preserves_correlation() -> None:
    command = _command(CommandType.APPLY_POLICY)
    request = ServiceRequest(
        protocol_version=2,
        request_id="req-1",
        command_id=command.command_id,
        operation=command.command_type,
        session_id=command.session_id,
        policy_hash=command.policy_hash,
        payload=command.payload,
        correlation_id=command.correlation_id,
    )
    result = ServiceResult(
        protocol_version=2,
        request_id=request.request_id,
        command_id=request.command_id,
        status=AckStatus.SUCCEEDED,
        applied_hash=HASH,
        service_version="2.0.0",
        occurred_at=NOW,
        correlation_id=request.correlation_id,
    )

    assert ServiceRequest.model_validate_json(request.to_json()) == request
    assert ServiceResult.model_validate_json(result.to_json()) == result
    assert result.correlation_id == command.correlation_id

    payload = request.model_dump(mode="json")
    payload["operation"] = "RUN_PROGRAM"
    with pytest.raises(ValidationError):
        ServiceRequest.model_validate(payload)


def test_error_codes_have_stable_human_descriptions() -> None:
    assert set(ERROR_DESCRIPTIONS) == set(ErrorCode)
    assert all(description.endswith(".") for description in ERROR_DESCRIPTIONS.values())


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("policy-envelope.json", PolicyEnvelope),
        ("apply-policy-command.json", Command),
        ("restore-baseline-command.json", Command),
        ("health-check-command.json", Command),
        ("ack-success.json", Ack),
        ("ack-failure.json", Ack),
        ("violation-event.json", Event),
        ("presence.json", Presence),
    ],
)
def test_checked_in_examples_validate(filename: str, model) -> None:
    encoded = (EXAMPLES / filename).read_text(encoding="utf-8")

    parsed = model.model_validate_json(encoded)

    assert model.model_validate_json(parsed.to_json()) == parsed


def test_backend_producer_and_shared_agent_consumer_use_one_contract() -> None:
    backend_command = _command(CommandType.APPLY_POLICY)
    wire_json = backend_command.to_json()

    agent_command = Command.model_validate_json(wire_json)

    assert agent_command == backend_command
    assert agent_command.payload.policy.policy_hash == HASH
