from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from agent.domain.identity import WorkstationIdentity


class AgentTransport(Protocol):
    def register(self, identity: WorkstationIdentity) -> None: ...
    def heartbeat(self, agent_id: str) -> None: ...


def run_agent(
    client: AgentTransport,
    identity: WorkstationIdentity,
    interval_seconds: int,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
    process_commands: Callable[[], None] | None = None,
) -> None:
    registered = False
    while True:
        try:
            if registered:
                client.heartbeat(identity.agent_id)
                log(f"Heartbeat sent for {identity.agent_id}")
            else:
                client.register(identity)
                registered = True
                log(
                    f"Registered {identity.agent_id} ({identity.hostname}) "
                    f"at {identity.ip_address}"
                )
            if process_commands is not None:
                process_commands()
        except OSError as exc:
            registered = False
            log(f"Agent request failed: {exc}; retrying registration")
        sleep(interval_seconds)
