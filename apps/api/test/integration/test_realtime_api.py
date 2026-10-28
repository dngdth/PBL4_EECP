import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.gateways import BindAgentToGatewayInput, RegisterGatewayInput
from app.application.security import Principal, Role
from app.domain.entities.exam_session import ExamSession
from app.domain.value_objects.enums import SessionState
from app.infrastructure.security import TokenService
from app.main import create_app
from app.presentation.api.routers.gateways import _handle_gateway_message
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from contracts.v2 import (
    Event,
    EventType,
    GatewayEnvelope,
    GatewayMessageType,
    Presence,
    PresenceHealth,
)

AUTH_KEY = "test-realtime-secret-key-at-least-32-bytes!!"


def _production_like_app(path: Path):
    app = create_app(path)
    app.state.settings = replace(app.state.settings, environment="production-like")
    app.state.token_service = TokenService(AUTH_KEY)
    return app


def _issue_token(
    app,
    *,
    subject: str = "test-user",
    role: Role = Role.EXAMINER,
    sessions: frozenset[str] = frozenset(),
) -> str:
    return app.state.token_service.issue(Principal(subject, role, sessions))


def test_websocket_connects_and_replies_to_ping(tmp_path: Path) -> None:
    with (
        TestClient(create_app(tmp_path / "realtime.db")) as client,
        client.websocket_connect("/api/v1/ws") as websocket,
    ):
        assert websocket.receive_json()["type"] == "connected"

        websocket.send_json({"type": "ping"})

        assert websocket.receive_json()["type"] == "pong"


def test_successful_api_mutation_is_broadcast(tmp_path: Path) -> None:
    with (
        TestClient(create_app(tmp_path / "broadcast.db")) as client,
        client.websocket_connect("/api/v1/ws") as websocket,
    ):
        assert websocket.receive_json()["type"] == "connected"

        response = client.post(
            "/api/v1/agents/register",
            json={
                "id": "PC01",
                "hostname": "DESKTOP-A",
                "ip_address": "192.168.3.56",
                "agent_version": "1.1.0",
            },
        )
        event = websocket.receive_json()

    assert response.status_code == 201
    assert event["type"] == "change"
    assert event["resource"] == "agents"
    assert event["action"] == "created"
    assert event["path"] == "/api/v1/agents/register"


def test_unauthorized_websocket_rejected_in_production_like(tmp_path: Path) -> None:
    app = _production_like_app(tmp_path / "prod-auth.db")
    with TestClient(app) as client:
        # 1. Missing token -> rejected with 1008
        with pytest.raises(WebSocketDisconnect) as exc1, client.websocket_connect("/api/v1/ws"):
            pass
        assert exc1.value.code == 1008

        # 2. Invalid token -> rejected with 1008
        with (
            pytest.raises(WebSocketDisconnect) as exc2,
            client.websocket_connect("/api/v1/ws?token=invalid.jwt.token"),
        ):
            pass
        assert exc2.value.code == 1008

        # 3. Valid token -> accepted
        valid_token = _issue_token(app, role=Role.ADMIN)
        with client.websocket_connect(f"/api/v1/ws?token={valid_token}") as ws:
            assert ws.receive_json()["type"] == "connected"


def test_examiner_session_scope_filters_events(tmp_path: Path) -> None:
    app = _production_like_app(tmp_path / "scope.db")
    token_ex1 = _issue_token(app, subject="ex1", role=Role.EXAMINER, sessions=frozenset({"SES-01"}))
    token_ex2 = _issue_token(app, subject="ex2", role=Role.EXAMINER, sessions=frozenset({"SES-02"}))
    token_admin = _issue_token(app, subject="admin", role=Role.ADMIN)

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/api/v1/ws?token={token_ex1}") as ws_ex1,
        client.websocket_connect(f"/api/v1/ws?token={token_ex2}") as ws_ex2,
        client.websocket_connect(f"/api/v1/ws?token={token_admin}") as ws_admin,
    ):
        assert ws_ex1.receive_json()["type"] == "connected"
        assert ws_ex2.receive_json()["type"] == "connected"
        assert ws_admin.receive_json()["type"] == "connected"

        # Broadcast event for SES-01
        hub = app.state.realtime_hub
        import asyncio
        asyncio.run(
            hub.publish_change(
                method="POST",
                path="/api/v1/sessions/SES-01",
                session_id="SES-01",
            )
        )

        event_ex1 = ws_ex1.receive_json()
        assert event_ex1["resource"] == "sessions"
        assert event_ex1["resource_id"] == "SES-01"

        event_admin = ws_admin.receive_json()
        assert event_admin["resource"] == "sessions"
        assert event_admin["resource_id"] == "SES-01"

        # ws_ex2 should NOT receive anything for SES-01; send a ping to verify it's still alive
        ws_ex2.send_json({"type": "ping"})
        reply = ws_ex2.receive_json()
        assert reply["type"] == "pong"


def test_reconnect_and_cleanup_without_leak(tmp_path: Path) -> None:
    app = create_app(tmp_path / "cleanup.db")
    hub = app.state.realtime_hub

    with TestClient(app) as client:
        assert len(hub._connections) == 0

        with client.websocket_connect("/api/v1/ws") as ws1:
            assert len(hub._connections) == 1
            ws1.receive_json()

        # After context exit, disconnected
        assert len(hub._connections) == 0

        # Reconnect
        with client.websocket_connect("/api/v1/ws") as ws2:
            assert len(hub._connections) == 1
            ws2.receive_json()

        assert len(hub._connections) == 0


def test_disconnect_does_not_break_backend_mutations(tmp_path: Path) -> None:
    app = create_app(tmp_path / "disconnect.db")
    with TestClient(app) as client:
        # Connect and immediately exit to simulate abrupt client disconnect
        with client.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()

        # Subsequent HTTP mutations must succeed normally without crashing hub
        resp = client.post(
            "/api/v1/agents/register",
            json={
                "id": "PC02",
                "hostname": "DESKTOP-B",
                "ip_address": "192.168.3.57",
                "agent_version": "1.1.0",
            },
        )
        assert resp.status_code == 201


def test_gateway_event_and_presence_broadcast_to_dashboard(tmp_path: Path) -> None:
    app = create_app(tmp_path / "gw-realtime.db")
    app.state.settings = replace(app.state.settings, gateway_bootstrap_token="test-gw-token")
    container = app.state.container

    # 1. Setup gateway and agent
    container.register_gateway(RegisterGatewayInput("GW-1", "LAB-1", "1.0.0"))
    container.register_agent(RegisterAgentInput("PC01", "DESKTOP-1", "192.168.1.10", "1.1.0"))
    container.bind_agent_to_gateway(BindAgentToGatewayInput("PC01", "GW-1"))

    # Create session and transition to RUNNING
    session = ExamSession.create("Midterm", "LAB-1", "GW-1", ["PC01"])
    session.id = "SES-001"
    session.state = SessionState.RUNNING
    with container.database.unit_of_work() as uow:
        uow.sessions.add(session)
        uow.commit()

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/v1/ws") as dashboard_ws,
    ):
        # Dashboard receives connected greeting
        assert dashboard_ws.receive_json()["type"] == "connected"

        # Gateway sends PRESENCE for PC01
        asyncio.run(
            _handle_gateway_message(
                "GW-1",
                GatewayEnvelope(
                    protocol_version=2,
                    message_type=GatewayMessageType.PRESENCE,
                    message_id="msg-2",
                    correlation_id="corr-2",
                    source_id="PC01",
                    target_id="backend",
                    payload=Presence(
                        protocol_version=2,
                        agent_id="PC01",
                        health=PresenceHealth.ONLINE,
                        active_policy_hash=None,
                        last_seen=datetime.now(UTC),
                    ).model_dump(mode="json"),
                ),
                container,
                app.state.presence,
                app.state.realtime_hub,
            )
        )

        # Dashboard receives presence invalidation!
        presence_event = dashboard_ws.receive_json()
        assert presence_event["type"] == "change"
        assert presence_event["resource"] == "agents"

        # Gateway sends EVENT (incident/telemetry)
        asyncio.run(
            _handle_gateway_message(
                "GW-1",
                GatewayEnvelope(
                    protocol_version=2,
                    message_type=GatewayMessageType.EVENT,
                    message_id="msg-3",
                    correlation_id="corr-3",
                    source_id="PC01",
                    target_id="backend",
                    payload=Event(
                        protocol_version=2,
                        event_id="evt-1",
                        session_id=session.id,
                        agent_id="PC01",
                        event_type=EventType.FORBIDDEN_PROCESS_DETECTED,
                        occurred_at=datetime.now(UTC),
                        payload={"severity": "CRITICAL", "category": "CHEAT_TOOL"},
                    ).model_dump(mode="json"),
                ),
                container,
                app.state.presence,
                app.state.realtime_hub,
            )
        )

        # Dashboard receives session invalidation!
        session_event = dashboard_ws.receive_json()
        assert session_event["type"] == "change"
        assert session_event["resource"] == "sessions"
        assert session_event["resource_id"] == session.id


def test_examiner_session_scope_enforcement_http_and_gateway(tmp_path: Path) -> None:
    app = _production_like_app(tmp_path / "scope-full.db")
    container = app.state.container

    token_ex_a = _issue_token(
        app, subject="ex_a", role=Role.EXAMINER, sessions=frozenset({"SES-A"})
    )
    token_ex_b = _issue_token(
        app, subject="ex_b", role=Role.EXAMINER, sessions=frozenset({"SES-B"})
    )
    token_admin = _issue_token(app, subject="admin", role=Role.ADMIN)

    # Register gateway & agents
    container.register_gateway(RegisterGatewayInput("GW-A", "LAB-A", "1.0.0"))
    container.register_agent(RegisterAgentInput("PC-A", "HOST-A", "192.168.1.1", "1.0"))
    container.register_agent(RegisterAgentInput("PC-B", "HOST-B", "192.168.1.2", "1.0"))
    container.bind_agent_to_gateway(BindAgentToGatewayInput("PC-A", "GW-A"))
    container.bind_agent_to_gateway(BindAgentToGatewayInput("PC-B", "GW-A"))

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/api/v1/ws?token={token_ex_a}") as ws_a,
        client.websocket_connect(f"/api/v1/ws?token={token_ex_b}") as ws_b,
        client.websocket_connect(f"/api/v1/ws?token={token_admin}") as ws_admin,
    ):
        assert ws_a.receive_json()["type"] == "connected"
        assert ws_b.receive_json()["type"] == "connected"
        assert ws_admin.receive_json()["type"] == "connected"

        hub = app.state.realtime_hub

        # 1. HTTP mutation for Session A
        asyncio.run(
            hub.publish_change(
                method="POST",
                path="/api/v1/sessions/SES-A/start",
            )
        )
        evt_a = ws_a.receive_json()
        assert evt_a["resource"] == "sessions"
        assert evt_a["resource_id"] == "SES-A"

        evt_admin = ws_admin.receive_json()
        assert evt_admin["resource_id"] == "SES-A"

        # ws_b must NOT receive SES-A event; verify with ping
        ws_b.send_json({"type": "ping"})
        assert ws_b.receive_json()["type"] == "pong"

        # 2. HTTP mutation for Session B
        asyncio.run(
            hub.publish_change(
                method="POST",
                path="/api/v1/sessions/SES-B/start",
            )
        )
        evt_b = ws_b.receive_json()
        assert evt_b["resource"] == "sessions"
        assert evt_b["resource_id"] == "SES-B"

        evt_admin_b = ws_admin.receive_json()
        assert evt_admin_b["resource_id"] == "SES-B"

        # ws_a must NOT receive SES-B event; verify with ping
        ws_a.send_json({"type": "ping"})
        assert ws_a.receive_json()["type"] == "pong"

        # 3. Gateway Uplink event for Session B
        asyncio.run(
            hub.publish_change(
                method="POST",
                path="/api/v1/sessions/SES-B",
                session_id="SES-B",
            )
        )
        evt_b_gw = ws_b.receive_json()
        assert evt_b_gw["resource_id"] == "SES-B"

        # ws_a must NOT receive it
        ws_a.send_json({"type": "ping"})
        assert ws_a.receive_json()["type"] == "pong"

        # 4. Event missing session_id (general agent update)
        asyncio.run(
            hub.publish_change(
                method="PATCH",
                path="/api/v1/agents",
            )
        )
        gen_a = ws_a.receive_json()
        gen_b = ws_b.receive_json()
        assert gen_a["resource"] == "agents"
        assert gen_a["resource_id"] is None
        assert gen_b["resource"] == "agents"
        assert gen_b["resource_id"] is None
        assert "SES-B" not in gen_a["path"]
