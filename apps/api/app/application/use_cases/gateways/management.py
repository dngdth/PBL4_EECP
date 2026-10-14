from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.application.dtos.gateways import (
    BindAgentToGatewayInput,
    GatewayDeliveryFailureInput,
    RegisterGatewayInput,
)
from app.domain.entities.gateway import AgentGatewayBinding, Gateway, GatewayStatus
from app.domain.entities.operations import Command
from app.domain.interfaces.unit_of_work import UnitOfWorkFactory
from app.domain.value_objects.enums import CommandStatus
from app.domain.value_objects.primitives import utc_now

Clock = Callable[[], datetime]


class RegisterGateway:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self, data: RegisterGatewayInput) -> Gateway:
        at = self._clock()
        with self._uow_factory() as uow:
            gateway = uow.gateways.find(data.gateway_id.strip())
            if gateway is None:
                gateway = Gateway.register(data.gateway_id, data.room_id, data.version, at)
                uow.gateways.add(gateway)
            else:
                gateway.reregister(data.room_id, data.version, at)
                uow.gateways.save(gateway)
            uow.commit()
            return gateway


class UpdateGatewayHealth:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(
        self,
        gateway_id: str,
        *,
        online: bool = True,
        connected_agent_count: int | None = None,
        backend_uplink_status: GatewayStatus = GatewayStatus.ONLINE,
        status: GatewayStatus = GatewayStatus.ONLINE,
    ) -> Gateway:
        with self._uow_factory() as uow:
            gateway = uow.gateways.get(gateway_id.strip())
            if online:
                if connected_agent_count is None:
                    gateway.heartbeat(self._clock())
                else:
                    gateway.update_health(
                        self._clock(), connected_agent_count, backend_uplink_status, status
                    )
            else:
                gateway.disconnect(self._clock())
            uow.gateways.save(gateway)
            uow.commit()
            return gateway


class BindAgentToGateway:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self, data: BindAgentToGatewayInput) -> AgentGatewayBinding:
        binding = AgentGatewayBinding(
            agent_id=data.agent_id.strip(),
            gateway_id=data.gateway_id.strip(),
            bound_at=self._clock(),
        )
        with self._uow_factory() as uow:
            uow.agents.get(binding.agent_id)
            uow.gateways.get(binding.gateway_id)
            uow.agent_gateway_bindings.bind(binding)
            uow.commit()
        return binding


class ResolveGatewayForAgent:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def __call__(self, agent_id: str) -> AgentGatewayBinding | None:
        with self._uow_factory() as uow:
            return uow.agent_gateway_bindings.find_for_agent(agent_id.strip())


class ListGateways:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def __call__(self) -> list[Gateway]:
        with self._uow_factory() as uow:
            return uow.gateways.list_all()


class ListAgentsForGateway:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def __call__(self, gateway_id: str) -> list[AgentGatewayBinding]:
        with self._uow_factory() as uow:
            return uow.agent_gateway_bindings.list_for_gateway(gateway_id.strip())


class RecordGatewayDeliveryFailure:
    """Record transport failure without pretending the Agent executed the command."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def __call__(self, data: GatewayDeliveryFailureInput) -> Command:
        with self._uow_factory() as uow:
            binding = uow.agent_gateway_bindings.find_for_agent(data.agent_id)
            if binding is None or binding.gateway_id != data.gateway_id:
                raise ValueError("delivery failure Agent is not bound to this Gateway")
            command = uow.commands.get(data.command_id)
            if command.target_id != data.agent_id or data.correlation_id != command.id:
                raise ValueError("delivery failure does not match the target command")
            if command.status not in {CommandStatus.PENDING, CommandStatus.DELIVERED}:
                return command
            reason = data.reason[:500]
            if command.error == reason:
                return command
            command.error = reason
            # Keep the existing next_retry_at, attempt limit and deadline. An
            # offline transport is not an execution ACK or a policy failure.
            uow.commands.save(command)
            uow.audits.append(
                command.session_id,
                actor=data.gateway_id,
                action="COMMAND_DELIVERY_FAILED",
                target=data.agent_id,
                details={"command_id": command.id, "reason": reason},
            )
            uow.commit()
            return command
