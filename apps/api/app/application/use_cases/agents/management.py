from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.application.dtos.agents import RegisterAgentInput
from app.domain.entities.agent import Agent
from app.domain.interfaces.unit_of_work import UnitOfWorkFactory
from app.domain.value_objects.primitives import utc_now

Clock = Callable[[], datetime]


class RegisterAgent:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self, data: RegisterAgentInput) -> Agent:
        at = self._clock()
        with self._uow_factory() as uow:
            agent = uow.agents.find(data.agent_id.strip())
            if agent is None:
                agent = Agent.register(
                    data.agent_id,
                    data.hostname,
                    data.ip_address,
                    data.agent_version,
                    at,
                )
                uow.agents.add(agent)
            else:
                agent.reregister(
                    data.hostname,
                    data.ip_address,
                    data.agent_version,
                    at,
                )
                uow.agents.save(agent)
            uow.commit()
            return agent


class HeartbeatAgent:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self, agent_id: str) -> Agent:
        with self._uow_factory() as uow:
            agent = uow.agents.get(agent_id)
            agent.heartbeat(self._clock())
            uow.agents.save(agent)
            uow.commit()
            return agent


class ListAgents:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = utc_now):
        self._uow_factory = uow_factory
        self._clock = clock

    def __call__(self) -> list[Agent]:
        at = self._clock()
        with self._uow_factory() as uow:
            agents = uow.agents.list_all()
            for agent in agents:
                if agent.refresh_liveness(at):
                    uow.agents.save(agent)
            uow.commit()
            return agents
