from datetime import UTC, datetime

import pytest
from app.domain.entities.operations import Command as DomainCommand
from app.domain.value_objects.enums import CommandType as DomainCommandType
from app.presentation.api.routers.gateways import _to_contract_command

from agent.application.policy_commands import PolicyCommandProcessor
from agent.client.infrastructure.gateway_control_client import GatewayControlClient
from agent.client.runtime import AgentClientRuntime
from agent.domain.identity import WorkstationIdentity
from agent.infrastructure.inprocess_executor import InProcessPrivilegedExecutor
from agent.infrastructure.policy_enforcement import AuditPolicyEnforcer
from agent.service.application.execution_service import ExecutionService
from contracts.v2 import (
    Ack,
    AckStatus,
    Command,
    GatewayEnvelope,
    GatewayMessageType,
    PolicyRules,
    compute_policy_hash,
)

NOW = datetime(2026, 11, 13, tzinfo=UTC)
COMMAND_KEY = "test-command-signing-key"
POLICY_KEY = "test-policy-signing-key"
AGENT_ID = "AGT-001"
SESSION_ID = "ses-finish-test"


class SessionObserver:
    def __init__(self):
        self.session_id = None

    def activate(self, session_id, _policy):
        self.session_id = session_id

    def deactivate(self):
        self.session_id = None

    def poll(self):
        pass


@pytest.mark.parametrize("restore_with_hash", [True, False])
@pytest.mark.parametrize("tamper_hash", [False, True])
def test_signed_restore_clears_policy_and_session_observers(
    tmp_path, monkeypatch, restore_with_hash, tamper_hash
):
    monkeypatch.setenv("EECP_COMMAND_SIGNING_KEY", COMMAND_KEY)
    monkeypatch.setenv("EECP_POLICY_SIGNING_KEY", POLICY_KEY)
    policy_hash = compute_policy_hash("TEST", 1, PolicyRules())
    state_path = tmp_path / "policy-state.json"
    service = ExecutionService(
        InProcessPrivilegedExecutor(AuditPolicyEnforcer(state_path), "test"),
        clock=lambda: NOW,
        require_signed_policy=True,
        policy_verification_key=POLICY_KEY,
        require_authorized_commands=True,
        command_verification_key=COMMAND_KEY,
        command_target_id=AGENT_ID,
    )
    client = GatewayControlClient("wss://gateway/ws/agents", "token", "test")
    # Supply identity without starting a network connection.
    identity = WorkstationIdentity(AGENT_ID, "HOST", "192.0.2.1", "test")
    client._identity = identity
    monitor, sensors, screen = (SessionObserver() for _ in range(3))
    processor = PolicyCommandProcessor(
        client, AGENT_ID, service, monitor=monitor, clock=lambda: NOW
    )
    runtime = AgentClientRuntime(client, identity, processor, monitor, 5, sensors, screen)

    def deliver(operation, payload):
        signed = _to_contract_command(
            DomainCommand(
                session_id=SESSION_ID,
                target_id=AGENT_ID,
                type=operation,
                payload=payload,
                created_at=NOW,
            )
        )
        # Use the same JSON contract boundary as the Gateway transport.
        wire_command = Command.model_validate_json(signed.to_json())
        if operation == DomainCommandType.RESTORE_BASELINE and tamper_hash:
            wire_command = wire_command.model_copy(update={"policy_hash": "f" * 64})
        client._receive(
            GatewayEnvelope(
                protocol_version=2,
                message_type=GatewayMessageType.COMMAND,
                message_id=f"msg_{signed.command_id}",
                correlation_id=signed.correlation_id,
                source_id="GW-A",
                target_id=AGENT_ID,
                payload=wire_command.model_dump(mode="json"),
            ).to_json()
        )
        runtime.process_control_cycle()
        return Ack.model_validate(client._outbound.get_nowait().payload)

    applied = deliver(
        DomainCommandType.APPLY_POLICY,
        {"profile": "TEST", "version": 1, "rules": {}, "policy_hash": policy_hash},
    )
    assert applied.status == AckStatus.SUCCEEDED
    assert state_path.exists()
    assert all(observer.session_id == SESSION_ID for observer in (monitor, sensors, screen))

    restore_payload = {"baseline": "NORMAL"}
    if restore_with_hash:
        restore_payload["policy_hash"] = policy_hash
    restored = deliver(DomainCommandType.RESTORE_BASELINE, restore_payload)

    if tamper_hash:
        assert restored.status == AckStatus.FAILED
        assert "authorization verification failed" in restored.error_message
        assert state_path.exists()
        assert service.active_session_id == SESSION_ID
        assert client.active_policy(AGENT_ID) is not None
    else:
        assert restored.status == AckStatus.SUCCEEDED
        assert not state_path.exists()
        assert service.active_session_id is None
        assert service.active_policy_hash is None
        assert client.active_policy(AGENT_ID) is None
        assert all(observer.session_id is None for observer in (monitor, sensors, screen))
        # Later control cycles must not reactivate the finished session.
        runtime.process_control_cycle()
        assert all(observer.session_id is None for observer in (monitor, sensors, screen))
