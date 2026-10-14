from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.gateways import RegisterGatewayInput
from app.application.dtos.policies import AcknowledgeCommandInput
from app.application.dtos.session_management import CreateExamSessionInput
from app.application.security import Permission, Principal, Role
from app.config import Settings
from app.main import create_app
from app.presentation.api.deps import authorize
from app.presentation.api.routers.gateways import _handle_gateway_message
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.gateway.app.config import GatewaySettings
from contracts.v2 import GatewayEnvelope, GatewayMessageType


def _audits(app, session_id: str | None = None):
    with app.state.container.database.unit_of_work() as uow:
        return uow.audits.list_for_session(session_id)


def _request(app, principal: Principal):
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(principal=principal),
        headers={"x-correlation-id": "CORR-SECURITY"},
    )


def _session(app) -> str:
    app.state.container.register_agent(
        RegisterAgentInput("AGT-AUDIT", "HOST-AUDIT", "192.0.2.10", "1.0")
    )
    return app.state.container.create_exam_session(
        CreateExamSessionInput("Security audit", "LAB-A", ["AGT-AUDIT"])
    ).session.id


def test_invalid_examiner_login_is_audited_without_password_or_token(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "login-audit.db")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "examiner-a", "password": "raw-password-secret"},
            headers={"Authorization": "Bearer raw-bearer-token"},
        )
    assert response.status_code == 401
    events = _audits(app)
    assert events[-1].action == "AUTHENTICATION_DENIED"
    encoded = json.dumps([event.details for event in events])
    assert "raw-password-secret" not in encoded
    assert "raw-bearer-token" not in encoded


def test_sensitive_authorization_denials_are_audited(tmp_path: Path) -> None:
    app = create_app(tmp_path / "authorization-audit.db")
    app.state.settings = replace(app.state.settings, environment="production-like")
    session_id = _session(app)
    cases = (
        (
            Principal("examiner-role", Role.EXAMINER, frozenset({session_id})),
            Permission.MANAGE_POLICY,
            None,
            None,
            "role does not grant permission",
        ),
        (
            Principal("examiner-scope", Role.EXAMINER, frozenset()),
            Permission.CONTROL_AGENT,
            session_id,
            None,
            "outside Examiner session scope",
        ),
        (
            Principal("examiner-state", Role.EXAMINER, frozenset({session_id})),
            Permission.CONTROL_AGENT,
            session_id,
            "COMPLETED",
            "session state does not allow control",
        ),
    )
    for principal, permission, scoped_session, state, message in cases:
        with pytest.raises(HTTPException, match=message):
            authorize(
                _request(app, principal), permission, session_id=scoped_session, session_state=state
            )

    events = _audits(app) + _audits(app, session_id)
    denials = [event for event in events if event.action == "AUTHORIZATION_DENIED"]
    assert {event.actor for event in denials} == {
        "examiner-role",
        "examiner-scope",
        "examiner-state",
    }
    assert all(event.details["decision"] == "DENY" for event in denials)
    assert all(event.details["correlation_id"] == "CORR-SECURITY" for event in denials)


def test_revoked_gateway_authentication_is_audited_without_credential(tmp_path: Path) -> None:
    app = create_app(tmp_path / "gateway-auth-audit.db")
    app.state.settings = replace(
        app.state.settings,
        gateway_credentials_json='{"GW-REVOKED":{"secret_sha256":"unused","revoked":true}}',
    )
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "/api/v2/gateways/ws/GW-REVOKED",
            headers={"Authorization": "Bearer raw-gateway-secret"},
        ),
    ):
        pass
    events = _audits(app)
    assert events[-1].details["reason_code"] == "REVOKED_CREDENTIAL"
    assert "raw-gateway-secret" not in json.dumps(events[-1].details)


def test_agent_security_event_reaches_backend_audit_chain(tmp_path: Path) -> None:
    app = create_app(tmp_path / "agent-auth-audit.db")
    envelope = GatewayEnvelope(
        protocol_version=2,
        message_type=GatewayMessageType.SECURITY_AUDIT,
        message_id="security-agent",
        correlation_id="agent-auth_AGT-REVOKED",
        source_id="GW-A",
        target_id="backend",
        payload={"actor_id": "AGT-REVOKED", "reason_code": "REVOKED_CREDENTIAL"},
    )
    asyncio.run(_handle_gateway_message("GW-A", envelope, app.state.container))
    events = _audits(app)
    assert events[-1].actor == "AGT-REVOKED"
    assert events[-1].details["reason_code"] == "REVOKED_CREDENTIAL"


def test_gateway_operational_dto_uses_backend_reported_buffer_fields(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "gateway-operational.db")
    app.state.container.register_gateway(RegisterGatewayInput("GW-A", "LAB-A", "2.1"))
    app.state.presence.set_gateway(
        "GW-A",
        {
            "pending_event_count": 7,
            "oldest_pending_event_age": 12.5,
            "last_flush_success_at": "2026-09-28T12:00:00Z",
            "last_flush_error": None,
            "buffer_status": "DEGRADED",
        },
    )
    with TestClient(app) as client:
        response = client.get("/api/v2/gateways")
    assert response.status_code == 200
    gateway = response.json()[0]
    assert gateway["gateway_id"] == "GW-A"
    assert gateway["pending_event_count"] == 7
    assert gateway["oldest_pending_event_age"] == 12.5
    assert gateway["buffer_status"] == "DEGRADED"
    assert gateway["last_flush_success_at"] == "2026-09-28T12:00:00Z"
    assert gateway["last_flush_error"] is None


def test_policy_signature_failure_ack_is_persisted_as_security_audit(tmp_path: Path) -> None:
    app = create_app(tmp_path / "signature-audit.db")
    session_id = _session(app)
    command = app.state.container.get_pending_commands("AGT-AUDIT")[0]
    app.state.container.acknowledge_command(
        AcknowledgeCommandInput(
            command_id=command.id,
            success=False,
            error="policy signature verification failed",
            actor="AGT-AUDIT",
        )
    )
    events = _audits(app, session_id)
    assert events[-1].action == "POLICY_VERIFICATION_FAILED"
    assert events[-1].details["reason_code"] == "INVALID_POLICY_SIGNATURE"
    assert events[-1].details["decision"] == "DENY"


def test_security_audit_hash_chain_detects_tampering(tmp_path: Path) -> None:
    app = create_app(tmp_path / "audit-chain.db")
    for index in range(3):
        app.state.container.security_audit.record(
            action="AUTHENTICATION_DENIED",
            actor=f"actor-{index}",
            actor_type="EXAMINER",
            resource_type="AUTH_SESSION",
            resource_id="login",
            reason_code="INVALID_CREDENTIALS",
        )
    with app.state.container.database.unit_of_work() as uow:
        assert uow.audits.verify_chain(None)
    with app.state.container.database.connect() as connection:
        connection.execute(
            "UPDATE audit_events SET details = ? WHERE sequence = 2",
            ('{"tampered":true}',),
        )
    with app.state.container.database.unit_of_work() as uow:
        assert not uow.audits.verify_chain(None)


def test_production_settings_reject_development_secrets(monkeypatch) -> None:
    values = {
        "EECP_ENVIRONMENT": "production-like",
        "DATABASE_URL": "postgresql://user:secret@db/eecp",
        "REDIS_URL": "redis://redis:6379/0",
        "EECP_GATEWAY_BOOTSTRAP_TOKEN": "test-secret",
        "EECP_AUTH_SIGNING_KEY": "a-secure-auth-key-that-is-long-enough",
        "EECP_EXAMINER_USERNAME": "examiner",
        "EECP_EXAMINER_PASSWORD_HASH": "pbkdf2-sha256$00$11",
        "EECP_GATEWAY_CREDENTIALS_JSON": ('{"GW-A":{"secret_sha256":"' + "a" * 64 + '"}}'),
        "EECP_POLICY_SIGNING_KEY": "a-secure-policy-key-that-is-long-enough",
        "EECP_COMMAND_SIGNING_KEY": "a-secure-command-key-that-is-long-enough",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="insecure development value"):
        Settings.from_env()


@pytest.mark.parametrize(
    "missing",
    [
        "DATABASE_URL",
        "REDIS_URL",
        "EECP_GATEWAY_BOOTSTRAP_TOKEN",
        "EECP_AUTH_SIGNING_KEY",
        "EECP_EXAMINER_USERNAME",
        "EECP_EXAMINER_PASSWORD_HASH",
        "EECP_GATEWAY_CREDENTIALS_JSON",
        "EECP_POLICY_SIGNING_KEY",
        "EECP_COMMAND_SIGNING_KEY",
    ],
)
def test_production_settings_reject_missing_required_configuration(
    monkeypatch, missing: str
) -> None:
    values = {
        "EECP_ENVIRONMENT": "production-like",
        "DATABASE_URL": "postgresql://user:secret@db/eecp",
        "REDIS_URL": "redis://redis:6379/0",
        "EECP_GATEWAY_BOOTSTRAP_TOKEN": "secure-gateway-bootstrap",
        "EECP_AUTH_SIGNING_KEY": "a-secure-auth-key-that-is-long-enough",
        "EECP_EXAMINER_USERNAME": "examiner",
        "EECP_EXAMINER_PASSWORD_HASH": "pbkdf2-sha256$00$11",
        "EECP_GATEWAY_CREDENTIALS_JSON": ('{"GW-A":{"secret_sha256":"' + "a" * 64 + '"}}'),
        "EECP_POLICY_SIGNING_KEY": "a-secure-policy-key-that-is-long-enough",
        "EECP_COMMAND_SIGNING_KEY": "a-secure-command-key-that-is-long-enough",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_production_gateway_rejects_plaintext_and_missing_identity_credentials() -> None:
    base = GatewaySettings(
        gateway_id="GW-A",
        room_id="LAB-A",
        version="1.0",
        listen_host="127.0.0.1",
        listen_port=8443,
        tls_certfile="gateway.crt",
        tls_keyfile="gateway.key",
        backend_ws_url="ws://backend/api/v2/gateways/ws",
        gateway_bootstrap_token="a-secure-gateway-token",
        agent_bootstrap_token="a-secure-agent-token",
        allow_plaintext_ws=True,
        reconnect_initial_seconds=1,
        reconnect_max_seconds=2,
        presence_timeout_seconds=15,
        health_interval_seconds=5,
        environment="production-like",
    )
    with pytest.raises(ValueError, match="requires WSS"):
        base.validate()
    with pytest.raises(ValueError, match="AGENT_CREDENTIALS_JSON"):
        replace(
            base, backend_ws_url="wss://backend/api/v2/gateways/ws", allow_plaintext_ws=False
        ).validate()
