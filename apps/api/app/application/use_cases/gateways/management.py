from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.application.dtos.gateways import (
    BindAgentToGatewayInput,
    RegisterGatewayInput,
)
from app.domain.entities.gateway import AgentGatewayBinding, Gateway, GatewayStatus
from app.domain.interfaces.unit_of_work import UnitOfWorkFactory
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
                gateway = Gateway.register(
                    data.gateway_id, data.room_id, data.version, at
                )
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
    ) -> Gateway:
        with self._uow_factory() as uow:
            gateway = uow.gateways.get(gateway_id.strip())
            if online:
                if connected_agent_count is None:
                    gateway.heartbeat(self._clock())
                else:
                    gateway.update_health(
                        self._clock(), connected_agent_count, backend_uplink_status
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
