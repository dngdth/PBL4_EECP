import time
from contextlib import ExitStack, suppress
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.gateway.app.config import GatewaySettings
from apps.gateway.app.main import create_app
from contracts.v2 import (
    Ack,
    AckStatus,
    AgentHello,
    GatewayEnvelope,
    GatewayMessageType,
    PresenceHealth,
)


class FakeUplink:
    def __init__(self):
        self.messages = []
        self.status = PresenceHealth.ONLINE

    async def start(self):
        return

    async def stop(self):
        return

    async def send(self, envelope):
        self.messages.append(envelope)


def _settings() -> GatewaySettings:
    return GatewaySettings(
        gateway_id="GW-A",
        room_id="LAB-A",
        version="1.0.0",
        listen_host="127.0.0.1",
        listen_port=8443,
        tls_certfile=None,
        tls_keyfile=None,
        backend_ws_url="ws://backend/api/v2/gateways/ws",
        gateway_bootstrap_token="gateway-token",
        agent_bootstrap_token="agent-token",
        allow_plaintext_ws=True,
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.1,
        presence_timeout_seconds=15,
        health_interval_seconds=5,
    )


def test_agent_websocket_handshake_presence_and_identity_binding() -> None:
    uplink = FakeUplink()
    with TestClient(create_app(_settings(), uplink)) as client:
        with client.websocket_connect(
            "/ws/agents", headers={"Authorization": "Bearer agent-token"}
        ) as websocket:
            hello = AgentHello(
                protocol_version=2,
                agent_id="AGT-001",
                hostname="HOST-1",
                ip_address="192.0.2.1",
                agent_version="1.1.0",
            )
            websocket.send_text(
                GatewayEnvelope(
                    protocol_version=2,
                    message_type=GatewayMessageType.AGENT_HELLO,
                    message_id="hello-1",
                    correlation_id="hello-1",
                    source_id="AGT-001",
                    target_id="gateway",
                    payload=hello.model_dump(mode="json"),
                ).to_json()
            )
            health = client.get("/health").json()
            assert health["connected_agent_count"] == 1

            ack = Ack(
                protocol_version=2,
                ack_id="ACK-1",
                command_id="CMD-1",
                status=AckStatus.SUCCEEDED,
                service_version="1.1.0",
                occurred_at=datetime.now(UTC),
                correlation_id="CORR-1",
            )
            websocket.send_text(
                GatewayEnvelope(
                    protocol_version=2,
                    message_type=GatewayMessageType.ACK,
                    message_id="ACK-1",
                    correlation_id="CORR-1",
                    source_id="AGT-002",
                    target_id="backend",
                    payload=ack.model_dump(mode="json"),
                ).to_json()
            )
            with suppress(Exception):
                websocket.receive_text()

        message_types = [message.message_type for message in uplink.messages]
        assert GatewayMessageType.AGENT_HELLO in message_types
        assert message_types.count(GatewayMessageType.PRESENCE) == 2


def test_three_agent_websocket_connections_are_tracked_independently() -> None:
    uplink = FakeUplink()
    with TestClient(create_app(_settings(), uplink)) as client, ExitStack() as stack:
        for index in range(1, 4):
            agent_id = f"AGT-00{index}"
            websocket = stack.enter_context(
                client.websocket_connect(
                    "/ws/agents",
                    headers={"Authorization": "Bearer agent-token"},
                )
            )
            hello = AgentHello(
                protocol_version=2,
                agent_id=agent_id,
                hostname=f"HOST-{index}",
                ip_address=f"192.0.2.{index}",
                agent_version="1.1.0",
            )
            websocket.send_text(
                GatewayEnvelope(
                    protocol_version=2,
                    message_type=GatewayMessageType.AGENT_HELLO,
                    message_id=f"hello-{index}",
                    correlation_id=f"hello-{index}",
                    source_id=agent_id,
                    target_id="gateway",
                    payload=hello.model_dump(mode="json"),
                ).to_json()
            )

        deadline = time.monotonic() + 2
        count = 0
        while count != 3 and time.monotonic() < deadline:
            count = client.get("/health").json()["connected_agent_count"]
            time.sleep(0.01)
        assert count == 3
