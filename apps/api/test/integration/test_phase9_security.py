from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.gateways import RegisterGatewayInput
from app.application.dtos.session_management import CreateExamSessionInput
from app.application.security import Principal, Role
from app.infrastructure.security import TokenService
from app.main import create_app
from fastapi.testclient import TestClient

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
