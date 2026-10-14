import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from app.application.dtos.agents import RegisterAgentInput
from app.application.dtos.gateways import BindAgentToGatewayInput, RegisterGatewayInput
from app.application.dtos.session_management import CreateExamSessionInput
from app.application.use_cases.policies.management import GetPendingCommands
from app.config import Settings
from app.infrastructure.di.container import build_container
from app.presentation.api.routers.gateways import _handle_gateway_message, _to_contract_command

from apps.gateway.app.application.routing import GatewayRouter
from apps.gateway.app.connections import AgentConnectionManager
from apps.gateway.app.presence import PresenceStore
from contracts.v2 import Ack, AckStatus, GatewayEnvelope, GatewayMessageType


def _setup(path):
    container = build_container(Settings(path))
    container.register_gateway(RegisterGatewayInput("GW-A", "LAB-A", "1"))
    container.register_agent(RegisterAgentInput("AGT-001", "HOST", "192.0.2.1", "1"))
    container.bind_agent_to_gateway(BindAgentToGatewayInput("AGT-001", "GW-A"))
    session = container.create_exam_session(CreateExamSessionInput("Exam", "LAB-A", ["AGT-001"]))
    command = container.get_pending_commands("AGT-001")[0]
    return container, session.session, command


def _offline_failure(command):
    class Backend:
        message = None

        async def send(self, envelope):
            self.message = envelope

    backend = Backend()
    router = GatewayRouter("GW-A", AgentConnectionManager(), PresenceStore(), backend)
    contract = _to_contract_command(command)
    asyncio.run(
        router.route_backend(
            GatewayEnvelope(
                protocol_version=2,
                message_type=GatewayMessageType.COMMAND,
                message_id=command.id,
                correlation_id=command.id,
                source_id="backend",
                target_id=command.target_id,
                payload=contract.model_dump(mode="json"),
            )
        )
    )
    return backend.message


def test_offline_delivery_is_audited_then_command_can_retry_and_succeed(tmp_path: Path):
    container, session, command = _setup(tmp_path / "delivery.db")
    failure = _offline_failure(command)
    asyncio.run(_handle_gateway_message("GW-A", failure, container))
    asyncio.run(_handle_gateway_message("GW-A", failure, container))
    with container.database.unit_of_work() as uow:
        pending = uow.commands.get(command.id)
        audits = uow.audits.list_for_session(session.id)
    assert pending.status.value == "DELIVERED"
    assert pending.acknowledged_at is None
    assert pending.next_retry_at is not None
    assert sum(a.action == "COMMAND_DELIVERY_FAILED" for a in audits) == 1

    # Advance the use-case clock, retaining the real retry/deadline semantics.
    retry_at = pending.next_retry_at + timedelta(milliseconds=1)
    retried = GetPendingCommands(container.database.unit_of_work, lambda: retry_at)("AGT-001")[0]
    assert retried.id == command.id
    assert retried.attempt_count == 2
    ack = Ack(
        protocol_version=2,
        ack_id="ack-1",
        command_id=command.id,
        status=AckStatus.SUCCEEDED,
        applied_hash=command.payload["policy_hash"],
        service_version="1",
        occurred_at=command.created_at,
        correlation_id=command.id,
    )
    asyncio.run(
        _handle_gateway_message(
            "GW-A",
            GatewayEnvelope(
                protocol_version=2,
                message_type=GatewayMessageType.ACK,
                message_id=ack.ack_id,
                correlation_id=command.id,
                source_id="AGT-001",
                target_id="backend",
                payload=ack.model_dump(mode="json"),
            ),
            container,
        )
    )
    # A delayed failure must not undo the successful ACK.
    asyncio.run(_handle_gateway_message("GW-A", failure, container))
    with container.database.unit_of_work() as uow:
        completed = uow.commands.get(command.id)
    assert completed.status.value == "ACKNOWLEDGED"
    assert completed.error is None


@pytest.mark.parametrize(
    "change",
    [
        {"source_id": "GW-OTHER"},
        {"target_id": "AGT-001"},
        {"correlation_id": "wrong-command"},
    ],
)
def test_delivery_failure_rejects_spoofed_identity_or_correlation(tmp_path: Path, change):
    container, _, command = _setup(tmp_path / "spoof.db")
    failure = _offline_failure(command).model_copy(update=change)
    with pytest.raises(ValueError):
        asyncio.run(_handle_gateway_message("GW-A", failure, container))
    with container.database.unit_of_work() as uow:
        assert uow.commands.get(command.id).error is None


def test_other_gateway_cannot_fail_an_agents_command(tmp_path: Path):
    container, _, command = _setup(tmp_path / "binding.db")
    failure = _offline_failure(command).model_copy(update={"source_id": "GW-B"})
    with pytest.raises(ValueError, match="not bound"):
        asyncio.run(_handle_gateway_message("GW-B", failure, container))
