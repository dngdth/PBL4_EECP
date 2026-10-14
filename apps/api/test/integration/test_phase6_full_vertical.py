from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.gateways import BindAgentToGatewayInput, RegisterGatewayInput
from app.config import Settings
from app.domain.entities.exam_session import ExamSession
from app.domain.entities.operations import Command as DomainCommand
from app.domain.value_objects.enums import (
    CommandStatus,
    SessionState,
)
from app.domain.value_objects.enums import (
    CommandType as DomainCommandType,
)
from app.infrastructure.di.container import build_container
from app.presentation.api.routers.gateways import (
    _handle_gateway_message,
    _route_pending_commands,
)

from agent.application.policy_commands import PolicyCommandProcessor
from agent.client.infrastructure.gateway_control_client import GatewayControlClient
from agent.client.infrastructure.named_pipe_executor import NamedPipePrivilegedExecutor
from agent.domain.identity import WorkstationIdentity
from agent.infrastructure.inprocess_executor import InProcessPrivilegedExecutor
from agent.infrastructure.policy_enforcement import WindowsPolicyEnforcer
from agent.infrastructure.windows.firewall_enforcer import FirewallRule, WindowsFirewallEnforcer
from agent.ipc.protocol import ServiceProtocolHandler, deserialize_result
from agent.service.application.execution_service import ExecutionService
from apps.gateway.app.connections import AgentConnectionManager
from apps.gateway.app.presence import PresenceStore
from apps.gateway.app.routing import GatewayRouter
from contracts.v2 import (
    Ack,
    AckStatus,
    GatewayEnvelope,
    GatewayMessageType,
    PolicyRules,
    compute_policy_hash,
)

NOW = datetime.now(UTC)


class NoOsMutationEnforcer:
    def apply(self, _payload):
        raise AssertionError("HEALTH_CHECK must not apply policy")

    def restore(self):
        raise AssertionError("HEALTH_CHECK must not restore policy")

    def maintain(self):
        return


class LoopbackNamedPipeTransport:
    """Byte-level loopback for the real named-pipe protocol boundary."""

    def __init__(self, handler: ServiceProtocolHandler):
        self._handler = handler
        self.requests: list[bytes] = []
        self.responses: list[bytes] = []

    def request(self, payload: bytes) -> bytes:
        self.requests.append(payload)
        response = self._handler(payload)
        self.responses.append(response)
        return response


class AgentSocket:
    def __init__(self, client: GatewayControlClient | None = None):
        self.client = client
        self.messages: list[str] = []

    async def send_text(self, data: str) -> None:
        self.messages.append(data)
        if self.client is not None:
            self.client._receive(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        return


class BackendCapture:
    def __init__(self):
        self.messages: list[GatewayEnvelope] = []

    async def send(self, envelope: GatewayEnvelope) -> None:
        self.messages.append(envelope)


class BackendRegistry:
    def __init__(self, router: GatewayRouter):
        self.router = router

    async def send(self, gateway_id: str, data: str) -> bool:
        assert gateway_id == "GW-A"
        await self.router.route_backend(GatewayEnvelope.model_validate_json(data))
        return True


class MemoryFirewall:
    def __init__(self):
        self._planner = WindowsFirewallEnforcer(platform="win32")
        self.rules: dict[str, FirewallRule] = {}
        self.restore_calls = 0

    def plan(self, session_id, blocked_ips, blocked_cidrs):
        return self._planner.plan(session_id, blocked_ips, blocked_cidrs)

    def reconcile(self, desired, current_rule_names):
        desired_names = {rule.name for rule in desired}
        for name in set(current_rule_names) - desired_names:
            self.rules.pop(name, None)
        self.rules.update({rule.name: rule for rule in desired})

    def remove_rules(self, rule_names):
        self.restore_calls += 1
        for name in rule_names:
            self.rules.pop(name, None)

    def verify_rules(self, rules):
        return all(self.rules.get(rule.name) == rule for rule in rules)


async def _vertical_round_trip(container, domain_command, enforcer):
    connections = AgentConnectionManager()
    backend = BackendCapture()
    gateway = GatewayRouter("GW-A", connections, PresenceStore(), backend)
    client = GatewayControlClient(
        "ws://gateway/ws/agents",
        "agent-token",
        "agent-1.1.0",
        allow_plaintext_ws=True,
    )
    client._identity = WorkstationIdentity(
        "AGT-001", "HOST-001", "192.0.2.1", "agent-1.1.0"
    )
    target_socket = AgentSocket(client)
    wrong_socket = AgentSocket()
    await connections.connect("AGT-001", target_socket)
    await connections.connect("AGT-002", wrong_socket)
    await _route_pending_commands("GW-A", container, BackendRegistry(gateway))

    inprocess = InProcessPrivilegedExecutor(enforcer, "service-2.0.0", clock=lambda: NOW)
    service = ExecutionService(inprocess, service_version="service-2.0.0", clock=lambda: NOW)
    pipe = LoopbackNamedPipeTransport(
        ServiceProtocolHandler(service.handle, "service-2.0.0", lambda: NOW)
    )
    PolicyCommandProcessor(
        client,
        "AGT-001",
        NamedPipePrivilegedExecutor(pipe, "agent-1.1.0", clock=lambda: NOW),
        clock=lambda: NOW,
    ).process_pending()
    ack_envelope = client._outbound.get_nowait()
    await gateway.route_agent("AGT-001", ack_envelope)
    returned = backend.messages.pop()
    await _handle_gateway_message("GW-A", returned, container)
    return (
        Ack.model_validate(ack_envelope.payload),
        GatewayEnvelope.model_validate_json(target_socket.messages[0]),
        wrong_socket,
        pipe,
    )


def _seed_backend(path: Path, *, include_health: bool = True):
    container = build_container(Settings(path))
    container.register_gateway(RegisterGatewayInput("GW-A", "LAB-A", "1.0.0"))
    for suffix in ("001", "002"):
        agent_id = f"AGT-{suffix}"
        container.register_agent(
            RegisterAgentInput(agent_id, f"HOST-{suffix}", f"192.0.2.{int(suffix)}", "1.1.0")
        )
        container.bind_agent_to_gateway(BindAgentToGatewayInput(agent_id, "GW-A"))

    session = ExamSession.create("Exam", "LAB-A", "GW-A", ["AGT-001", "AGT-002"])
    command = DomainCommand(
        id="CMD-HEALTH-001",
        session_id=session.id,
        target_id="AGT-001",
        type=DomainCommandType.HEALTH_CHECK,
        payload={"nonce": "phase-6"},
        created_at=NOW,
    )
    with container.database.unit_of_work() as uow:
        uow.sessions.add(session)
        if include_health:
            uow.commands.add_many([command])
        uow.commit()
    return container, command


def test_health_check_full_vertical_round_trip(tmp_path: Path) -> None:
    container, domain_command = _seed_backend(tmp_path / "phase6-health.db")
    connections = AgentConnectionManager()
    backend = BackendCapture()
    gateway = GatewayRouter("GW-A", connections, PresenceStore(), backend)

    client = GatewayControlClient(
        "ws://gateway/ws/agents",
        "agent-token",
        "agent-1.1.0",
        allow_plaintext_ws=True,
    )
    client._identity = WorkstationIdentity("AGT-001", "HOST-001", "192.0.2.1", "agent-1.1.0")
    target_socket = AgentSocket(client)
    wrong_socket = AgentSocket()
    asyncio.run(connections.connect("AGT-001", target_socket))
    asyncio.run(connections.connect("AGT-002", wrong_socket))

    asyncio.run(_route_pending_commands("GW-A", container, BackendRegistry(gateway)))
    assert len(target_socket.messages) == 1
    assert wrong_socket.messages == []

    inprocess = InProcessPrivilegedExecutor(
        NoOsMutationEnforcer(), "service-2.0.0", clock=lambda: NOW
    )
    service = ExecutionService(inprocess, service_version="service-2.0.0", clock=lambda: NOW)
    pipe = LoopbackNamedPipeTransport(
        ServiceProtocolHandler(service.handle, "service-2.0.0", lambda: NOW)
    )
    processor = PolicyCommandProcessor(
        client,
        "AGT-001",
        NamedPipePrivilegedExecutor(pipe, "agent-1.1.0", clock=lambda: NOW),
        clock=lambda: NOW,
    )
    processor.process_pending()

    ack_envelope = client._outbound.get_nowait()
    ack = Ack.model_validate(ack_envelope.payload)
    routed_command = GatewayEnvelope.model_validate_json(target_socket.messages[0])
    assert len(pipe.requests) == 1
    assert ack.command_id == domain_command.id
    assert ack.status == AckStatus.SUCCEEDED
    assert ack.correlation_id == routed_command.correlation_id == domain_command.id
    assert ack_envelope.correlation_id == domain_command.id
    assert ack.service_version == "service-2.0.0"

    asyncio.run(gateway.route_agent("AGT-001", ack_envelope))
    returned = backend.messages.pop()
    assert returned.message_type == GatewayMessageType.ACK
    assert returned.target_id == "backend"
    asyncio.run(_handle_gateway_message("GW-A", returned, container))

    with container.database.unit_of_work() as uow:
        persisted = uow.commands.get(domain_command.id)
        audits = uow.audits.list_for_session(domain_command.session_id)
    assert persisted.status == CommandStatus.ACKNOWLEDGED
    assert persisted.target_id == "AGT-001"
    assert any(
        event.action == "COMMAND_ACKNOWLEDGED"
        and event.details["command_id"] == domain_command.id
        for event in audits
    )


def test_apply_policy_full_vertical_preserves_hash_and_backend_state(tmp_path: Path) -> None:
    container, _ = _seed_backend(tmp_path / "phase6-apply.db", include_health=False)
    rules = {
        "network": {
            "blocked_domains": ["blocked.example"],
            "blocked_ips": ["203.0.113.10"],
            "blocked_cidrs": ["198.51.100.0/24"],
        }
    }
    with container.database.unit_of_work() as uow:
        session = uow.sessions.list_all()[0]
        policy = session.deploy_policy("NETWORK_TEST", rules)
        command = DomainCommand(
            id="CMD-APPLY-001",
            session_id=session.id,
            target_id="AGT-001",
            type=DomainCommandType.APPLY_POLICY,
            payload={
                "profile": policy.profile,
                "version": policy.version,
                "policy_hash": policy.policy_hash,
                "rules": policy.rules,
            },
            created_at=NOW,
        )
        uow.sessions.save(session)
        uow.commands.add_many([command])
        uow.commit()

    firewall = MemoryFirewall()
    hosts = tmp_path / "hosts-apply"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    enforcer = WindowsPolicyEnforcer(
        tmp_path / "apply-state.json",
        hosts_path=hosts,
        runner=lambda arguments, **_kwargs: subprocess.CompletedProcess(
            arguments, 0, stdout="", stderr=""
        ),
        firewall=firewall,
    )

    ack, routed, wrong_socket, pipe = asyncio.run(
        _vertical_round_trip(container, command, enforcer)
    )

    assert wrong_socket.messages == []
    assert len(pipe.requests) == 1
    assert routed.correlation_id == command.id
    assert ack.command_id == command.id
    assert ack.correlation_id == command.id
    assert ack.applied_hash == policy.policy_hash
    assert deserialize_result(pipe.responses[0]).applied_hash == policy.policy_hash
    assert {rule.remote_address for rule in firewall.rules.values()} == {
        "203.0.113.10",
        "198.51.100.0/24",
    }
    with container.database.unit_of_work() as uow:
        persisted = uow.commands.get(command.id)
        stored_session = uow.sessions.get(session.id)
    assert persisted.status == CommandStatus.ACKNOWLEDGED
    assert stored_session.workstations["AGT-001"].actual_policy_hash == policy.policy_hash


def test_restore_baseline_full_vertical_removes_owned_firewall_rules(tmp_path: Path) -> None:
    container, _ = _seed_backend(tmp_path / "phase6-restore.db", include_health=False)
    with container.database.unit_of_work() as uow:
        session = uow.sessions.list_all()[0]
        session.state = SessionState.RESTORING
        command = DomainCommand(
            id="CMD-RESTORE-001",
            session_id=session.id,
            target_id="AGT-001",
            type=DomainCommandType.RESTORE_BASELINE,
            payload={"baseline": "NORMAL"},
            created_at=NOW,
        )
        uow.sessions.save(session)
        uow.commands.add_many([command])
        uow.commit()

    firewall = MemoryFirewall()
    hosts = tmp_path / "hosts-restore"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    enforcer = WindowsPolicyEnforcer(
        tmp_path / "restore-state.json",
        hosts_path=hosts,
        runner=lambda arguments, **_kwargs: subprocess.CompletedProcess(
            arguments, 0, stdout="", stderr=""
        ),
        firewall=firewall,
    )
    rules = {
        "network": {"blocked_ips": ["203.0.113.10"]},
    }
    seed_payload = {
        "format": "eecp-policy/v1",
        "profile": "NETWORK_TEST",
        "version": 1,
        "session_id": session.id,
        "policy_hash": compute_policy_hash(
            "NETWORK_TEST", 1, PolicyRules.model_validate(rules)
        ),
        "rules": rules,
    }
    enforcer.apply(seed_payload)
    assert firewall.rules

    ack, _routed, wrong_socket, _pipe = asyncio.run(
        _vertical_round_trip(container, command, enforcer)
    )

    assert wrong_socket.messages == []
    assert ack.command_id == command.id
    assert ack.status == AckStatus.SUCCEEDED
    assert firewall.rules == {}
    assert firewall.restore_calls == 1
    with container.database.unit_of_work() as uow:
        persisted = uow.commands.get(command.id)
        stored_session = uow.sessions.get(session.id)
    assert persisted.status == CommandStatus.ACKNOWLEDGED
    assert stored_session.workstations["AGT-001"].restored is True
