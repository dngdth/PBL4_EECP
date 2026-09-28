from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.gateways import RegisterGatewayInput
from app.application.dtos.session_management import CreateExamSessionInput
from app.application.security import Principal, Role
from app.domain.entities.operations import Command as DomainCommand
from app.domain.value_objects.enums import CommandType as DomainCommandType
from app.infrastructure.security import TokenService
from app.main import create_app
from app.presentation.api.routers.gateways import _to_contract_command
from fastapi.testclient import TestClient

from agent.client.infrastructure.gateway_control_client import _legacy_command
from contracts.v2 import CommandType, ServiceRequest, compute_command_authorization

KEY = "phase-9-http-auth-key-that-is-at-least-thirty-two-bytes"


def _production_like_app(path: Path):
    app = create_app(path)
    app.state.settings = replace(app.state.settings, environment="production-like")
    app.state.token_service = TokenService(KEY)
    return app


def _token(
    app,
    *,
    role: Role = Role.EXAMINER,
    sessions: frozenset[str] = frozenset(),
) -> str:
    return app.state.token_service.issue(Principal("examiner", role, sessions))


def test_all_examiner_http_prefixes_require_authentication(tmp_path: Path) -> None:
    app = _production_like_app(tmp_path / "http-auth.db")
    with TestClient(app) as client:
        assert client.get("/api/v2/gateways").status_code == 401
        assert client.get("/api/v2/gateways/agents/AGT-001").status_code == 401
        assert client.get("/api/v1/agents").status_code == 401
        assert client.get("/api/v1/sessions").status_code == 401
        assert client.get("/api/v1/policy-profiles").status_code == 401
        assert client.get("/health").status_code == 200


def test_invalid_expired_and_machine_credentials_cannot_access_examiner_api(
    tmp_path: Path,
) -> None:
    app = _production_like_app(tmp_path / "http-invalid.db")
    expired = app.state.token_service.issue(
        Principal("expired", Role.ADMIN, frozenset()),
        datetime.now(UTC) - timedelta(hours=2),
    )
    with TestClient(app) as client:
        for credential in ("invalid-token", "gateway-machine-secret", expired):
            response = client.get(
                "/api/v2/gateways",
                headers={"Authorization": f"Bearer {credential}"},
            )
            assert response.status_code == 401


def test_http_authorization_enforces_role_and_session_scope(tmp_path: Path) -> None:
    app = _production_like_app(tmp_path / "http-scope.db")
    app.state.container.register_agent(
        RegisterAgentInput("AGT-1", "HOST-1", "192.0.2.1", "1.0")
    )
    session_id = app.state.container.create_exam_session(
        CreateExamSessionInput("Exam", "LAB", ["AGT-1"])
    ).session.id
    examiner = _token(app, sessions=frozenset())
    admin = _token(app, role=Role.ADMIN)
    with TestClient(app) as client:
        outside_scope = client.get(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {examiner}"},
        )
        wrong_role = client.post(
            "/api/v1/policy-profiles",
            headers={"Authorization": f"Bearer {examiner}"},
            json={
                "id": "CUSTOM",
                "label": "Custom",
                "description": "Custom policy",
                "rules": {},
            },
        )
        app.state.container.register_gateway(RegisterGatewayInput("GW-1", "LAB", "1.0"))
        allowed = client.get(
            "/api/v2/gateways",
            headers={"Authorization": f"Bearer {admin}"},
        )
    assert outside_scope.status_code == 403
    assert wrong_role.status_code == 403
    assert allowed.status_code == 200


def test_backend_signs_and_client_preserves_privileged_command_context(
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    key = "phase-9-command-key-with-sufficient-entropy"
    monkeypatch.setenv("EECP_COMMAND_SIGNING_KEY", key)
    domain = DomainCommand(
        id="CMD-RESTORE-1",
        session_id="SES-1",
        target_id="AGT-1",
        type=DomainCommandType.RESTORE_BASELINE,
        payload={"baseline": "NORMAL"},
        created_at=now,
        expires_at=now + timedelta(minutes=1),
    )

    command = _to_contract_command(domain)
    forwarded = _legacy_command(command)
    request = ServiceRequest(
        protocol_version=2,
        request_id="REQ-1",
        command_id=forwarded["id"],
        operation=CommandType(forwarded["type"]),
        session_id=forwarded["session_id"],
        target_id=forwarded["target_id"],
        issued_at=forwarded["issued_at"],
        deadline=forwarded["deadline"],
        policy_hash=None,
        payload={"baseline": "NORMAL"},
        correlation_id=forwarded["correlation_id"],
        authorization=forwarded["authorization"],
    )

    expected = compute_command_authorization(
        key,
        protocol_version=2,
        command_id=request.command_id,
        operation=request.operation,
        session_id=request.session_id,
        target_id=request.target_id,
        policy_hash=request.policy_hash,
        issued_at=request.issued_at,
        deadline=request.deadline,
        correlation_id=request.correlation_id,
    )
    assert command.authorization == forwarded["authorization"] == request.authorization
    assert request.authorization == expected
