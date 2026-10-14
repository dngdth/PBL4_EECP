from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application.security import (
    AuthorizationDeniedError,
    AuthorizationService,
    Permission,
    Principal,
    Role,
)
from app.infrastructure.persistence.postgres import PostgresDatabase
from app.infrastructure.presence import MemoryPresenceStore
from app.infrastructure.security import (
    MachineCredentialRegistry,
    TokenService,
    hash_password,
    verify_password,
)

from agent.service.application.execution_service import ExecutionService
from contracts.v2 import (
    AckStatus,
    ApplyPolicyPayload,
    CommandType,
    PolicyEnvelope,
    PolicyRules,
    ServiceRequest,
    ServiceResult,
    compute_policy_hash,
    compute_policy_signature,
    verify_policy_signature,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
KEY = "phase-8-test-key-that-is-at-least-thirty-two-bytes"


def test_examiner_token_expiry_role_and_session_scope() -> None:
    tokens = TokenService(KEY, lifetime_seconds=60)
    examiner = Principal("examiner-a", Role.EXAMINER, frozenset({"SES-A"}))
    token = tokens.issue(examiner, NOW)
    assert tokens.verify(token, NOW + timedelta(seconds=59)) == examiner
    with pytest.raises(ValueError, match="expired"):
        tokens.verify(token, NOW + timedelta(seconds=60))

    authorization = AuthorizationService()
    authorization.require(examiner, Permission.CONTROL_AGENT, session_id="SES-A")
    with pytest.raises(AuthorizationDeniedError, match="scope"):
        authorization.require(examiner, Permission.CONTROL_AGENT, session_id="SES-B")
    with pytest.raises(AuthorizationDeniedError, match="state"):
        authorization.require(
            examiner,
            Permission.CONTROL_AGENT,
            session_id="SES-A",
            session_state="COMPLETED",
        )
    with pytest.raises(AuthorizationDeniedError, match="role"):
        authorization.require(examiner, Permission.MANAGE_POLICY)


def test_password_hash_and_machine_credentials_support_identity_and_revocation() -> None:
    encoded = hash_password("exam-password", salt=b"0123456789abcdef")
    assert verify_password("exam-password", encoded)
    assert not verify_password("wrong", encoded)

    secret = "agent-secret"
    digest = hashlib.sha256(secret.encode()).hexdigest()
    registry = MachineCredentialRegistry(
        '{"AGT-1":{"secret_sha256":"'
        + digest
        + '"},"AGT-2":{"secret_sha256":"'
        + digest
        + '","revoked":true}}'
    )
    assert registry.authenticate("AGT-1", secret)
    assert not registry.authenticate("AGT-OTHER", secret)
    assert not registry.authenticate("AGT-1", "wrong")
    assert not registry.authenticate("AGT-2", secret)


def test_signed_policy_rejects_unsigned_or_tampered_signature() -> None:
    rules = PolicyRules.model_validate({"network": {"blocked_domains": ["example.com"]}})
    policy_hash = compute_policy_hash("LOCKDOWN", 1, rules)
    signature = compute_policy_signature(KEY, "LOCKDOWN", 1, rules, "SES-1")
    signed = PolicyEnvelope(
        protocol_version=2,
        policy_id="LOCKDOWN",
        policy_version=1,
        policy_hash=policy_hash,
        session_id="SES-1",
        issued_at=NOW,
        rules=rules,
        signature=signature,
    )
    assert verify_policy_signature(signed, KEY)
    assert not verify_policy_signature(signed.model_copy(update={"signature": "bad"}), KEY)
    assert not verify_policy_signature(signed.model_copy(update={"signature": None}), KEY)
    assert not verify_policy_signature(signed.model_copy(update={"session_id": "SES-2"}), KEY)


def test_privileged_service_rejects_unsigned_policy_before_executor() -> None:
    class Executor:
        calls = 0

        def execute(self, request):
            self.calls += 1
            return ServiceResult(
                protocol_version=2,
                request_id=request.request_id,
                command_id=request.command_id,
                status=AckStatus.SUCCEEDED,
                applied_hash=request.policy_hash,
                service_version="2.0",
                occurred_at=NOW,
                correlation_id=request.correlation_id,
            )

    rules = PolicyRules()
    policy = PolicyEnvelope(
        protocol_version=2,
        policy_id="LOCKDOWN",
        policy_version=1,
        policy_hash=compute_policy_hash("LOCKDOWN", 1, rules),
        session_id="SES-1",
        issued_at=NOW,
        rules=rules,
    )
    request = ServiceRequest(
        protocol_version=2,
        request_id="REQ-1",
        command_id="CMD-1",
        operation=CommandType.APPLY_POLICY,
        session_id="SES-1",
        policy_hash=policy.policy_hash,
        payload=ApplyPolicyPayload(policy=policy),
        correlation_id="CORR-1",
    )
    executor = Executor()
    service = ExecutionService(
        executor,
        policy_verification_key=KEY,
        require_signed_policy=True,
    )

    result = service.handle(request)

    assert result.status == AckStatus.REJECTED
    assert executor.calls == 0


def test_memory_presence_ttl_expiry_does_not_hold_business_data() -> None:
    store = MemoryPresenceStore(ttl_seconds=30)
    store.set_agent("AGT-1", {"health": "ONLINE"})
    assert store.get_agent("AGT-1") == {"health": "ONLINE"}
    created, value = store._values["presence:agent:AGT-1"]
    store._values["presence:agent:AGT-1"] = (created - timedelta(seconds=31), value)
    assert store.get_agent("AGT-1") is None


def test_postgresql_migration_declares_business_invariants() -> None:
    migration = (
        Path(__file__).parents[2]
        / "app"
        / "infrastructure"
        / "persistence"
        / "migrations"
        / "0001_initial.sql"
    ).read_text(encoding="utf-8")
    assert "telemetry_events (id TEXT PRIMARY KEY" in migration
    assert "UNIQUE(session_id, agent_id)" in migration
    assert "audit_events" in migration
    with pytest.raises(ValueError, match="PostgreSQL"):
        PostgresDatabase("sqlite:///wrong.db")
