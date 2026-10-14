import queue
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from agent.client import main as client_main
from agent.client.infrastructure.gateway_control_client import GatewayControlClient
from agent.domain.identity import WorkstationIdentity
from contracts.v2 import (
    Ack,
    Command,
    CommandType,
    GatewayEnvelope,
    GatewayMessageType,
    HealthCheckPayload,
    Presence,
    PresenceHealth,
    ServiceHealth,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


class FakeConnection:
    def __init__(self):
        self.incoming = queue.Queue()
        self.sent: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def send(self, message):
        self.sent.append(message)

    def recv(self, timeout=None):
        try:
            return self.incoming.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None


def test_agent_gateway_client_receives_command_and_preserves_ack_correlation() -> None:
    connection = FakeConnection()
    client = GatewayControlClient(
        "ws://gateway/ws/agents",
        "agent-token",
        "1.1.0",
        allow_plaintext_ws=True,
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.02,
        connector=lambda *_args, **_kwargs: connection,
        jitter=lambda: 0,
    )
    identity = WorkstationIdentity("AGT-001", "HOST", "192.0.2.1", "1.1.0")
    client.register(identity)
    command = Command(
        protocol_version=2,
        command_id="CMD-001",
        command_type=CommandType.HEALTH_CHECK,
        target_id="AGT-001",
        session_id="SES-001",
        issued_at=NOW,
        deadline=NOW + timedelta(minutes=1),
        payload=HealthCheckPayload(nonce="health"),
        correlation_id="CORR-001",
    )
    connection.incoming.put(
        GatewayEnvelope(
            protocol_version=2,
            message_type=GatewayMessageType.COMMAND,
            message_id="MSG-001",
            correlation_id="CORR-001",
            source_id="GW-A",
            target_id="AGT-001",
            payload=command.model_dump(mode="json"),
        ).to_json()
    )
    deadline = time.monotonic() + 1
    commands = []
    while not commands and time.monotonic() < deadline:
        commands = client.pending_commands("AGT-001")
        time.sleep(0.01)
    assert commands[0]["id"] == "CMD-001"

    client.acknowledge_command("CMD-001", success=True, actor="AGT-001")
    deadline = time.monotonic() + 1
    while len(connection.sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    ack_envelope = GatewayEnvelope.model_validate_json(connection.sent[-1])
    ack = Ack.model_validate(ack_envelope.payload)
    assert ack.command_id == "CMD-001"
    assert ack.correlation_id == "CORR-001"
    assert ack_envelope.correlation_id == "CORR-001"
    client.close()


def test_agent_production_entrypoint_uses_gateway_not_direct_backend(monkeypatch) -> None:
    captured = []

    class Runtime:
        def run(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(client_main, "load_agent_id", lambda: "AGT-001")
    monkeypatch.setattr(client_main, "NamedPipeClient", lambda _config: object())
    monkeypatch.setattr(
        client_main,
        "NamedPipePrivilegedExecutor",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        client_main,
        "build_client_runtime",
        lambda *args: captured.append(args) or Runtime(),
    )
    monkeypatch.setattr(
        client_main,
        "NamedPipeClientConfig",
        lambda **_kwargs: SimpleNamespace(),
    )

    client_main.main([])

    assert captured[0][2] is None


def test_agent_gateway_client_reconnects_with_backoff() -> None:
    connection = FakeConnection()
    attempts = 0

    def connector(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("Gateway restarting")
        return connection

    client = GatewayControlClient(
        "ws://gateway/ws/agents",
        "agent-token",
        "1.1.0",
        allow_plaintext_ws=True,
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.02,
        connector=connector,
        jitter=lambda: 0,
    )

    client.register(WorkstationIdentity("AGT-001", "HOST", "192.0.2.1", "1.1.0"))

    assert attempts >= 2
    assert GatewayEnvelope.model_validate_json(connection.sent[0]).message_type == (
        GatewayMessageType.AGENT_HELLO
    )
    client.close()


def test_agent_presence_is_degraded_when_service_is_unavailable() -> None:
    connection = FakeConnection()
    client = GatewayControlClient(
        "ws://gateway/ws/agents",
        "agent-token",
        "1.1.0",
        allow_plaintext_ws=True,
        connector=lambda *_args, **_kwargs: connection,
    )
    client.register(WorkstationIdentity("AGT-001", "HOST", "192.0.2.1", "1.1.0"))
    client.set_service_health(ServiceHealth.UNAVAILABLE, None)
    client.heartbeat("AGT-001")
    deadline = time.monotonic() + 1
    while len(connection.sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    envelope = GatewayEnvelope.model_validate_json(connection.sent[-1])
    presence = Presence.model_validate(envelope.payload)
    assert presence.health == PresenceHealth.DEGRADED
    assert presence.service_health == ServiceHealth.UNAVAILABLE
    client.close()
