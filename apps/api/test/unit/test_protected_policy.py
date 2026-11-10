import json

import pytest

from agent.infrastructure.policy_enforcement import AuditPolicyEnforcer
from agent.infrastructure.protected_policy import ProtectedPolicyEnforcer
from contracts.v2 import PolicyRules, compute_policy_hash


def policy(version=1, rules=None):
    rules = rules or PolicyRules()
    return {
        "profile": "DEFAULT",
        "version": version,
        "session_id": "ses1",
        "policy_hash": compute_policy_hash("DEFAULT", version, rules),
        "rules": rules.model_dump(mode="json", exclude_none=True),
    }


def guard(tmp_path, inner=None):
    state = tmp_path / "policy.json"
    return ProtectedPolicyEnforcer(
        inner or AuditPolicyEnforcer(state), state, tmp_path / "protected.json", "service-only-key"
    )


def test_policy_v2_rejects_v1_even_after_restart_and_restore(tmp_path):
    enforcer = guard(tmp_path)
    enforcer.apply(policy(2))
    enforcer.restore()
    restarted = guard(tmp_path)
    with pytest.raises(ValueError, match="rollback"):
        restarted.apply(policy(1))
    assert not (tmp_path / "policy.json").exists()


def test_offline_old_file_restore_recovers_last_known_good_and_logs(tmp_path):
    enforcer = guard(tmp_path)
    enforcer.apply(policy(1))
    old_state = (tmp_path / "policy.json").read_bytes()
    enforcer.apply(policy(2))
    (tmp_path / "policy.json").write_bytes(old_state)
    restarted = guard(tmp_path)
    events = []
    restarted.set_event_sink(lambda **values: events.append(values))
    restarted.maintain()
    assert (
        json.loads((tmp_path / "policy.json").read_text())["policy_hash"]
        == policy(2)["policy_hash"]
    )
    assert any(value["action"] == "STATE_RECOVERED" for value in events)


def test_modified_rules_same_version_are_rejected(tmp_path):
    enforcer = guard(tmp_path)
    enforcer.apply(policy())
    changed = policy(rules=PolicyRules.model_validate({"devices": {"usb": "deny"}}))
    with pytest.raises(ValueError, match="same-version"):
        enforcer.apply(changed)


def test_journal_mac_is_verified_at_restart(tmp_path):
    enforcer = guard(tmp_path)
    enforcer.apply(policy())
    path = tmp_path / "protected.json"
    value = json.loads(path.read_text())
    value["value"]["watermarks"] = {}
    path.write_text(json.dumps(value))
    with pytest.raises(OSError, match="authentication"):
        guard(tmp_path)


def test_safe_apply_restores_previous_snapshot_after_partial_failure(tmp_path):
    class FailingEnforcer(AuditPolicyEnforcer):
        def apply(self, payload):
            result = super().apply(payload)
            if payload["version"] == 2:
                raise OSError("injected failure after state write")
            return result

    enforcer = guard(tmp_path, FailingEnforcer(tmp_path / "policy.json"))
    enforcer.apply(policy())
    with pytest.raises(OSError, match="injected"):
        enforcer.apply(policy(2))
    assert (
        json.loads((tmp_path / "policy.json").read_text())["policy_hash"] == policy()["policy_hash"]
    )
    assert guard(tmp_path).value["watermarks"]["ses1"]["version"] == 1


def test_old_successful_command_cache_cannot_reactivate_old_client_policy(tmp_path):
    from datetime import UTC, datetime

    from agent.service.runtime import AgentServiceRuntime
    from contracts.v2 import (
        AckStatus,
        ApplyPolicyPayload,
        PolicyEnvelope,
        ServiceRequest,
        compute_policy_signature,
    )

    key = "service-only-key"
    runtime = AgentServiceRuntime.build(
        policy_mode="audit",
        state_path=tmp_path / "state.json",
        service_version="1",
        policy_verification_key=key,
        require_signed_policy=True,
        state_guard_path=tmp_path / "protected.json",
    )

    def command(version):
        values = policy(version)
        rules = PolicyRules()
        envelope = PolicyEnvelope(
            protocol_version=2,
            policy_id="DEFAULT",
            policy_version=version,
            policy_hash=values["policy_hash"],
            session_id="ses1",
            issued_at=datetime.now(UTC),
            rules=rules,
            signature=compute_policy_signature(key, "DEFAULT", version, rules, "ses1"),
        )
        return ServiceRequest(
            protocol_version=2,
            request_id=f"request_{version}",
            command_id=f"command_{version}",
            operation="APPLY_POLICY",
            session_id="ses1",
            policy_hash=envelope.policy_hash,
            payload=ApplyPolicyPayload(policy=envelope),
            correlation_id=f"command_{version}",
        )

    first, second = command(1), command(2)
    assert runtime.execution_service.handle(first).status == AckStatus.SUCCEEDED
    assert runtime.execution_service.handle(second).status == AckStatus.SUCCEEDED
    assert runtime.execution_service.handle(first).status == AckStatus.REJECTED
    assert runtime.execution_service.active_policy_hash == second.policy_hash
