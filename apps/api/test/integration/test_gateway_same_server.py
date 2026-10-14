"""Real WSS sockets on one host; no privileged Windows mutations."""

import asyncio
import hashlib
import json
import shutil
import socket
import ssl
import subprocess
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import uvicorn
from app.application.dtos.session_management import (
    CreateExamSessionInput,
    UpdateExamSessionStatusInput,
)
from app.domain.value_objects.enums import SessionState
from app.main import create_app as create_backend
from websockets.asyncio.client import connect

from apps.gateway.app.config import GatewaySettings
from apps.gateway.app.main import create_app as create_gateway
from contracts.v2 import (
    Ack,
    AckStatus,
    AgentHello,
    Command,
    Event,
    EventType,
    GatewayEnvelope,
    GatewayMessageType,
    PresenceHealth,
)


async def _wait(predicate, timeout=15):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


class RunningServer:
    def __init__(self, app, cert: str, key: str):
        self.app, self.cert, self.key = app, cert, key
        self.port = 0
        self.task = None
        self.server = None

    async def start(self):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port))
        self.port = listener.getsockname()[1]
        listener.listen()
        listener.setblocking(False)
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host="127.0.0.1",
                port=self.port,
                log_level="error",
                ssl_certfile=self.cert,
                ssl_keyfile=self.key,
                timeout_graceful_shutdown=2,
            )
        )
        self.task = asyncio.create_task(self.server.serve(sockets=[listener]))
        await _wait(lambda: self.server.started)

    async def stop(self):
        if self.task is not None:
            self.server.should_exit = True
            await asyncio.wait_for(self.task, 8)
            self.task = None


@pytest.fixture
def certificate(tmp_path: Path):
    executable = shutil.which("openssl")
    if executable is None:
        pytest.skip("OpenSSL is required for the real WSS same-server test")
    cert, key = tmp_path / "server.crt", tmp_path / "server.key"
    subprocess.run(
        [
            executable,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1,DNS:gateway-backend-tls",
        ],
        check=True,
        capture_output=True,
    )
    return str(cert), str(key)


def _message(kind, source, payload, identifier):
    return GatewayEnvelope(
        protocol_version=2,
        message_type=kind,
        message_id=identifier,
        correlation_id=identifier,
        source_id=source,
        target_id="backend",
        payload=payload,
    )


def test_30_agents_over_wss_keep_lan_connections_and_flush_events_after_backend_restart(
    tmp_path: Path,
    monkeypatch,
    certificate,
):
    gateway_secret = "isolated-gateway-machine-credential"
    monkeypatch.setenv("EECP_GATEWAY_BOOTSTRAP_TOKEN", gateway_secret)
    monkeypatch.setenv(
        "EECP_GATEWAY_CREDENTIALS_JSON",
        json.dumps(
            {
                "GW-A": {"secret_sha256": hashlib.sha256(gateway_secret.encode()).hexdigest()},
            }
        ),
    )
    cert, key = certificate
    backend_app = create_backend(tmp_path / "business.db")
    backend_app.state.settings = replace(
        backend_app.state.settings,
        gateway_credentials_json=json.dumps(
            {
                "GW-A": {"secret_sha256": hashlib.sha256(gateway_secret.encode()).hexdigest()},
            }
        ),
    )

    async def scenario():
        backend = RunningServer(backend_app, cert, key)
        await backend.start()
        agents = {f"PC{i:02}": f"isolated-machine-credential-{i}" for i in range(1, 31)}
        settings = GatewaySettings(
            gateway_id="GW-A",
            room_id="LAB-A",
            version="1",
            listen_host="127.0.0.1",
            listen_port=8443,
            tls_certfile=cert,
            tls_keyfile=key,
            backend_ws_url=f"wss://127.0.0.1:{backend.port}/api/v2/gateways/ws",
            backend_ca_file=cert,
            gateway_bootstrap_token=gateway_secret,
            agent_bootstrap_token="isolated-required-compatibility-value",
            allow_plaintext_ws=False,
            environment="production-like",
            agent_credentials_json=json.dumps(
                {
                    agent: {"secret_sha256": hashlib.sha256(secret.encode()).hexdigest()}
                    for agent, secret in agents.items()
                }
            ),
            reconnect_initial_seconds=0.05,
            reconnect_max_seconds=0.2,
            presence_timeout_seconds=60,
            presence_offline_timeout_seconds=90,
            health_interval_seconds=0.1,
            event_buffer_path=str(tmp_path / "gateway-events.db"),
            event_retry_initial_seconds=0.1,
            event_retry_max_seconds=0.5,
            event_flush_interval_seconds=0.02,
        )
        settings.validate()
        gateway_app = create_gateway(settings)
        gateway = RunningServer(gateway_app, cert, key)
        try:
            await gateway.start()
            url = f"wss://127.0.0.1:{gateway.port}/ws/agents"
            trusted = ssl.create_default_context(cafile=cert)
            with pytest.raises(ssl.SSLCertVerificationError):
                async with connect(url, ssl=ssl.create_default_context()):
                    pass
            async with AsyncExitStack() as stack:
                sockets = {}
                for agent, secret in agents.items():
                    peer = await stack.enter_async_context(
                        connect(
                            url,
                            ssl=trusted,
                            additional_headers={"Authorization": f"Bearer {secret}"},
                        )
                    )
                    sockets[agent] = peer
                    hello = AgentHello(
                        protocol_version=2,
                        agent_id=agent,
                        hostname=agent,
                        ip_address="192.0.2.1",
                        agent_version="1",
                    )
                    await peer.send(
                        _message(
                            GatewayMessageType.AGENT_HELLO,
                            agent,
                            hello.model_dump(mode="json"),
                            f"hello-{agent}",
                        ).to_json()
                    )
                container = backend_app.state.container
                await _wait(lambda: len(container.list_agents_for_gateway("GW-A")) == 30)
                session = container.create_exam_session(
                    CreateExamSessionInput("Same server exam", "LAB-A", ["PC07"]),
                ).session
                raw = await asyncio.wait_for(sockets["PC07"].recv(), 10)
                command = Command.model_validate(GatewayEnvelope.model_validate_json(raw).payload)
                assert command.target_id == "PC07"

                async def receives_nothing(peer):
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(peer.recv(), 0.05)

                await asyncio.gather(
                    *[receives_nothing(peer) for agent, peer in sockets.items() if agent != "PC07"]
                )
                ack = Ack(
                    protocol_version=2,
                    ack_id="ack-same-server",
                    command_id=command.command_id,
                    status=AckStatus.SUCCEEDED,
                    applied_hash=command.policy_hash,
                    service_version="simulated-service",
                    occurred_at=datetime.now(UTC),
                    correlation_id=command.correlation_id,
                )
                envelope = _message(
                    GatewayMessageType.ACK, "PC07", ack.model_dump(mode="json"), ack.ack_id
                )
                await sockets["PC07"].send(
                    envelope.model_copy(
                        update={"correlation_id": command.correlation_id},
                    ).to_json()
                )

                def acknowledged():
                    with container.database.unit_of_work() as uow:
                        return uow.commands.get(command.command_id).status.value == "ACKNOWLEDGED"

                await _wait(acknowledged)
                for state in (SessionState.READY, SessionState.RUNNING):
                    container.update_exam_session_status(
                        UpdateExamSessionStatusInput(session.id, state, "teacher"),
                    )
                await backend.stop()
                await _wait(lambda: gateway_app.state.uplink.status != PresenceHealth.ONLINE)
                assert len(await gateway_app.state.connections.list_connected()) == 30
                for i in range(3):
                    event = Event(
                        protocol_version=2,
                        event_id=f"EVT-OFFLINE-{i}",
                        agent_id="PC07",
                        session_id=session.id,
                        event_type=EventType.POLICY_VIOLATION,
                        occurred_at=datetime.now(UTC),
                        correlation_id=f"event-{i}",
                        payload={
                            "severity": "WARNING",
                            "category": "PROHIBITED_WEBSITE",
                            "action": "BLOCKED",
                            "destination": "example.invalid",
                        },
                    )
                    await sockets["PC07"].send(
                        _message(
                            GatewayMessageType.EVENT,
                            "PC07",
                            event.model_dump(mode="json"),
                            f"event-{i}",
                        ).to_json()
                    )
                await _wait(lambda: gateway_app.state.event_buffer.count() == 3)
                assert all(peer.close_code is None for peer in sockets.values())
                # Recreate Backend state from the same business database, rather
                # than retaining its in-memory socket registry or presence.
                backend.app = create_backend(tmp_path / "business.db")
                backend.app.state.settings = backend_app.state.settings
                await backend.start()
                container = backend.app.state.container
                await _wait(lambda: gateway_app.state.event_buffer.count() == 0)
                assert len(await gateway_app.state.connections.list_connected()) == 30
                with container.database.unit_of_work() as uow:
                    events = uow.telemetry.list_for_session(session.id)
                    incidents = uow.incidents.list_for_session(session.id)
                assert {e.id for e in events} == {f"EVT-OFFLINE-{i}" for i in range(3)}
                assert len(incidents) == 3
                assert all(peer.close_code is None for peer in sockets.values())

                # Pending Events also survive a full Gateway app recreation.
                await backend.stop()
                await _wait(lambda: gateway_app.state.uplink.status != PresenceHealth.ONLINE)
                event = Event(
                    protocol_version=2,
                    event_id="EVT-GATEWAY-RESTART",
                    agent_id="PC07",
                    session_id=session.id,
                    event_type=EventType.POLICY_VIOLATION,
                    occurred_at=datetime.now(UTC),
                    correlation_id="event-restart",
                    payload={
                        "severity": "WARNING",
                        "category": "PROHIBITED_WEBSITE",
                        "action": "BLOCKED",
                        "destination": "example.invalid",
                    },
                )
                await sockets["PC07"].send(
                    _message(
                        GatewayMessageType.EVENT,
                        "PC07",
                        event.model_dump(mode="json"),
                        "event-restart",
                    ).to_json()
                )
                await _wait(lambda: gateway_app.state.event_buffer.count() == 1)
                await gateway.stop()
                gateway_app = create_gateway(settings)
                gateway.app = gateway_app
                await gateway.start()
                assert gateway_app.state.event_buffer.count() == 1
                reconnected = {}
                for agent, secret in agents.items():
                    peer = await stack.enter_async_context(
                        connect(
                            url,
                            ssl=trusted,
                            additional_headers={"Authorization": f"Bearer {secret}"},
                        )
                    )
                    reconnected[agent] = peer
                    hello = AgentHello(
                        protocol_version=2,
                        agent_id=agent,
                        hostname=agent,
                        ip_address="192.0.2.1",
                        agent_version="1",
                    )
                    await peer.send(
                        _message(
                            GatewayMessageType.AGENT_HELLO,
                            agent,
                            hello.model_dump(mode="json"),
                            f"hello-restart-{agent}",
                        ).to_json()
                    )
                backend.app = create_backend(tmp_path / "business.db")
                backend.app.state.settings = backend_app.state.settings
                await backend.start()
                container = backend.app.state.container
                await _wait(lambda: gateway_app.state.event_buffer.count() == 0)
                assert len(await gateway_app.state.connections.list_connected()) == 30
                assert all(peer.close_code is None for peer in reconnected.values())
                with container.database.unit_of_work() as uow:
                    assert len(uow.telemetry.list_for_session(session.id)) == 4
                    assert len(uow.incidents.list_for_session(session.id)) == 4
        finally:
            await gateway.stop()
            await backend.stop()

    asyncio.run(scenario())
