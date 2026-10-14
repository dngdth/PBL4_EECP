from app.main import create_app
from fastapi.testclient import TestClient


def test_websocket_connects_and_replies_to_ping(tmp_path) -> None:
    with (
        TestClient(create_app(tmp_path / "realtime.db")) as client,
        client.websocket_connect("/api/v1/ws") as websocket,
    ):
        assert websocket.receive_json()["type"] == "connected"

        websocket.send_json({"type": "ping"})

        assert websocket.receive_json()["type"] == "pong"


def test_successful_api_mutation_is_broadcast(tmp_path) -> None:
    with (
        TestClient(create_app(tmp_path / "broadcast.db")) as client,
        client.websocket_connect("/api/v1/ws") as websocket,
    ):
        websocket.receive_json()

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
