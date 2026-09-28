from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from agent.application.policy_commands import PolicyCommandProcessor
from agent.application.runtime import run_agent
from agent.domain.identity import WorkstationIdentity


class BackendConnection(Protocol):
    def register(self, identity: WorkstationIdentity) -> None: ...

    def heartbeat(self, agent_id: str) -> None: ...

    def active_policy(self, agent_id: str) -> tuple[str, dict[str, Any]] | None: ...


class PolicyMonitor(Protocol):
    def activate(self, session_id: str, payload: dict[str, Any]) -> None: ...

    def deactivate(self) -> None: ...


class AgentClientRuntime:
    """Own backend communication, monitoring, and command orchestration."""

    def __init__(
        self,
        backend: BackendConnection,
        identity: WorkstationIdentity,
        command_processor: PolicyCommandProcessor,
        monitor: PolicyMonitor,
        heartbeat_interval_seconds: int,
    ):
        self._backend = backend
        self._identity = identity
        self._command_processor = command_processor
        self._monitor = monitor
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    def run(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        run_agent(
            self._backend,
            self._identity,
            self._heartbeat_interval_seconds,
            sleep=sleep,
            log=log,
            process_commands=self.process_control_cycle,
        )

    def process_control_cycle(self) -> None:
        self._command_processor.process_pending()
        active_policy = self._backend.active_policy(self._identity.agent_id)
        if active_policy is None:
            self._monitor.deactivate()
        else:
            session_id, policy = active_policy
            self._monitor.activate(session_id, policy)
