from __future__ import annotations

import queue
import random
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from agent.domain.identity import WorkstationIdentity
from contracts.v2 import (
    Ack,
    AckStatus,
    AgentHello,
    ApplyPolicyPayload,
    Command,
    CommandType,
    ErrorCode,
    Event,
    EventType,
    GatewayEnvelope,
    GatewayMessageType,
    Presence,
    PresenceHealth,
    ServiceHealth,
)


class SyncWebSocket(Protocol):
    def __enter__(self) -> SyncWebSocket: ...
    def __exit__(self, exc_type, exc_value, traceback) -> bool | None: ...
    def send(self, message: str) -> None: ...
    def recv(self, timeout: float | None = None) -> str | bytes: ...


Connector = Callable[..., SyncWebSocket]


class GatewayControlClient:
    """Persistent normal-user Agent control-plane transport through Local Gateway."""

    def __init__(
        self,
        gateway_url: str,
        bootstrap_token: str,
        agent_version: str,
        *,
        allow_plaintext_ws: bool = False,
        reconnect_initial_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        connector: Connector = connect,
        jitter: Callable[[], float] = random.random,
    ):
        scheme = urlparse(gateway_url).scheme
        if scheme not in {"ws", "wss"}:
            raise ValueError("Gateway URL must use ws:// or wss://")
        if scheme == "ws" and not allow_plaintext_ws:
            raise ValueError("plaintext ws:// Gateway transport is dev-only")
        if not bootstrap_token:
            raise ValueError("Gateway bootstrap token is required")
        self._gateway_url = gateway_url
        self._bootstrap_token = bootstrap_token
        self._agent_version = agent_version
        self._reconnect_initial = reconnect_initial_seconds
        self._reconnect_max = reconnect_max_seconds
        self._connector = connector
        self._jitter = jitter
        self._identity: WorkstationIdentity | None = None
        self._connected = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._outbound: queue.Queue[GatewayEnvelope] = queue.Queue(maxsize=1000)
        self._commands: queue.Queue[Command] = queue.Queue()
        self._command_index: dict[str, Command] = {}
        self._active_policy: tuple[str, dict[str, Any]] | None = None
        self._state_lock = threading.Lock()
        self._event_sequence = 0
        self._service_health = ServiceHealth.HEALTHY
        self._service_policy_hash: str | None = None

    def register(self, identity: WorkstationIdentity) -> None:
        self._identity = identity
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="eecp-agent-gateway",
                daemon=True,
            )
            self._thread.start()
        if not self._connected.wait(timeout=5.0):
            raise OSError("Local Gateway is unavailable")

    def heartbeat(self, agent_id: str) -> None:
        self._require_identity(agent_id)
        if not self._connected.is_set():
            raise OSError("Local Gateway connection is unavailable")
        presence = Presence(
            protocol_version=2,
            agent_id=agent_id,
            last_seen=datetime.now(UTC),
            health=(
                PresenceHealth.ONLINE
                if self._service_health == ServiceHealth.HEALTHY
                else PresenceHealth.DEGRADED
            ),
            active_policy_hash=self._service_policy_hash,
            service_health=self._service_health,
            agent_version=self._agent_version,
        )
        self._queue(
            GatewayMessageType.PRESENCE,
            f"presence_{agent_id}_{int(presence.last_seen.timestamp() * 1000)}",
            presence.model_dump(mode="json", exclude_none=True),
        )

    def pending_commands(self, agent_id: str) -> list[dict[str, Any]]:
        self._require_identity(agent_id)
        values: list[dict[str, Any]] = []
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return values
            values.append(_legacy_command(command))

    def acknowledge_command(
        self,
        command_id: str,
        *,
        success: bool,
        policy_hash: str | None = None,
        error: str | None = None,
        actor: str,
        service_version: str | None = None,
    ) -> None:
        self._require_identity(actor)
        command = self._command_index.get(command_id)
        correlation_id = command.correlation_id if command else command_id
        ack = Ack(
            protocol_version=2,
            ack_id=f"ack_{command_id}",
            command_id=command_id,
            status=AckStatus.SUCCEEDED if success else AckStatus.FAILED,
            applied_hash=policy_hash,
            service_version=service_version or self._agent_version,
            error_code=None if success else ErrorCode.EXECUTION_FAILED,
            error_message=None if success else error or "execution failed",
            occurred_at=datetime.now(UTC),
            correlation_id=correlation_id,
        )
        if success and command is not None:
            with self._state_lock:
                if command.command_type == CommandType.APPLY_POLICY:
                    payload = command.payload
                    if isinstance(payload, ApplyPolicyPayload):
                        policy = payload.policy
                        self._active_policy = (
                            command.session_id,
                            {
                                "format": "eecp-policy/v1",
                                "policy_hash": policy.policy_hash,
                                "version": policy.policy_version,
                                "profile": policy.policy_id,
                                "rules": policy.rules.model_dump(
                                    mode="json", exclude_none=True
                                ),
                                "signature": policy.signature,
                            },
                        )
                elif command.command_type == CommandType.RESTORE_BASELINE:
                    self._active_policy = None
        self._queue(
            GatewayMessageType.ACK,
            ack.ack_id,
            ack.model_dump(mode="json", exclude_none=True),
            correlation_id=correlation_id,
        )

    def active_policy(self, agent_id: str) -> tuple[str, dict[str, Any]] | None:
        self._require_identity(agent_id)
        with self._state_lock:
            return self._active_policy

    def report_policy_violation(
        self, session_id: str, agent_id: str, destination: str
    ) -> None:
        self.report_event(
            session_id,
            agent_id,
            EventType.POLICY_VIOLATION,
            {
                "severity": "WARNING",
                "category": "PROHIBITED_WEBSITE",
                "action": "BLOCKED",
                "destination": destination,
                "source": "agent-loopback-monitor",
            },
        )

    def report_event(
        self,
        session_id: str,
        agent_id: str,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> None:
        self._require_identity(agent_id)
        with self._state_lock:
            self._event_sequence += 1
            sequence = self._event_sequence
        event_id = f"evt_{uuid.uuid4().hex}"
        event = Event(
            protocol_version=2,
            event_id=event_id,
            session_id=session_id,
            agent_id=agent_id,
            event_type=event_type,
            occurred_at=datetime.now(UTC),
            sequence=sequence,
            payload=payload,
            correlation_id=f"event_{session_id}",
        )
        self._queue(
            GatewayMessageType.EVENT,
            event.event_id,
            event.model_dump(mode="json", exclude_none=True),
            correlation_id=event.correlation_id or event.event_id,
        )

    def set_service_health(
        self, service_health: ServiceHealth, active_policy_hash: str | None
    ) -> None:
        with self._state_lock:
            self._service_health = service_health
            self._service_policy_hash = active_policy_hash

    def close(self) -> None:
        self._stop.set()
        self._connected.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        delay = self._reconnect_initial
        while not self._stop.is_set():
            try:
                with self._connector(
                    self._gateway_url,
                    additional_headers={
                        "Authorization": f"Bearer {self._bootstrap_token}"
                    },
                    open_timeout=5,
                ) as websocket:
                    websocket.send(self._hello().to_json())
                    self._connected.set()
                    delay = self._reconnect_initial
                    while not self._stop.is_set():
                        self._flush_outbound(websocket)
                        try:
                            message = websocket.recv(timeout=0.1)
                        except TimeoutError:
                            continue
                        self._receive(message)
            except (ConnectionClosed, OSError, TimeoutError, ValueError):
                self._connected.clear()
                wait = min(self._reconnect_max, delay * (1 + 0.25 * self._jitter()))
                self._stop.wait(wait)
                delay = min(self._reconnect_max, delay * 2)
        self._connected.clear()

    def _hello(self) -> GatewayEnvelope:
        if self._identity is None:
            raise ValueError("Agent identity is unavailable")
        hello = AgentHello(
            protocol_version=2,
            agent_id=self._identity.agent_id,
            hostname=self._identity.hostname,
            ip_address=self._identity.ip_address,
            agent_version=self._identity.agent_version,
        )
        return GatewayEnvelope(
            protocol_version=2,
            message_type=GatewayMessageType.AGENT_HELLO,
            message_id=f"hello_{hello.agent_id}",
            correlation_id=f"hello_{hello.agent_id}",
            source_id=hello.agent_id,
            target_id="gateway",
            payload=hello.model_dump(mode="json"),
        )

    def _flush_outbound(self, websocket: SyncWebSocket) -> None:
        while True:
            try:
                envelope = self._outbound.get_nowait()
            except queue.Empty:
                return
            websocket.send(envelope.to_json())

    def _receive(self, raw: str | bytes) -> None:
        envelope = GatewayEnvelope.model_validate_json(raw)
        if envelope.message_type != GatewayMessageType.COMMAND:
            raise ValueError("Gateway sent an unsupported message type")
        command = Command.model_validate(envelope.payload)
        if self._identity is None or command.target_id != self._identity.agent_id:
            raise ValueError("Gateway command target does not match Agent identity")
        if envelope.correlation_id != command.correlation_id:
            raise ValueError("Gateway command correlation does not match payload")
        self._command_index[command.command_id] = command
        self._commands.put_nowait(command)

    def _queue(
        self,
        message_type: GatewayMessageType,
        message_id: str,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> None:
        if self._identity is None:
            raise OSError("Agent is not registered with Local Gateway")
        try:
            self._outbound.put_nowait(
                GatewayEnvelope(
                    protocol_version=2,
                    message_type=message_type,
                    message_id=message_id,
                    correlation_id=correlation_id or message_id,
                    source_id=self._identity.agent_id,
                    target_id="backend",
                    payload=payload,
                )
            )
        except queue.Full as exc:
            raise OSError("Agent Gateway queue is full; message was not retained") from exc

    def _require_identity(self, agent_id: str) -> None:
        if self._identity is None or self._identity.agent_id != agent_id:
            raise OSError("Agent identity does not match Gateway connection")


def _legacy_command(command: Command) -> dict[str, Any]:
    payload = command.payload
    if isinstance(payload, ApplyPolicyPayload):
        policy = payload.policy
        legacy_payload: dict[str, Any] = {
            "format": "eecp-policy/v1",
            "policy_hash": policy.policy_hash,
            "version": policy.policy_version,
            "profile": policy.policy_id,
            "rules": policy.rules.model_dump(mode="json", exclude_none=True),
            "signature": policy.signature,
        }
    else:
        legacy_payload = payload.model_dump(mode="json", exclude_none=True)
    return {
        "id": command.command_id,
        "session_id": command.session_id,
        "type": command.command_type.value,
        "payload": legacy_payload,
        "correlation_id": command.correlation_id,
    }
