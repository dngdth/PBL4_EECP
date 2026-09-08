from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class CommandClient(Protocol):
    def pending_commands(self, agent_id: str) -> list[dict[str, Any]]: ...

    def acknowledge_command(
        self,
        command_id: str,
        *,
        success: bool,
        policy_hash: str | None = None,
        error: str | None = None,
        actor: str,
    ) -> None: ...


class PolicyEnforcer(Protocol):
    def apply(self, payload: dict[str, Any]) -> str: ...
    def restore(self) -> None: ...
    def maintain(self) -> None: ...


class PolicyMonitor(Protocol):
    def activate(self, session_id: str, payload: dict[str, Any]) -> None: ...
    def deactivate(self) -> None: ...


class PolicyCommandProcessor:
    def __init__(
        self,
        client: CommandClient,
        agent_id: str,
        enforcer: PolicyEnforcer,
        monitor: PolicyMonitor | None = None,
        log: Callable[[str], None] = print,
    ):
        self._client = client
        self._agent_id = agent_id
        self._enforcer = enforcer
        self._monitor = monitor
        self._log = log

    def process_pending(self) -> None:
        for command in self._client.pending_commands(self._agent_id):
            self._execute(command)
        self._enforcer.maintain()

    def _execute(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("id", ""))
        session_id = str(command.get("session_id", ""))
        command_type = command.get("type")
        payload = command.get("payload")
        if not command_id or not isinstance(payload, dict):
            self._log("Ignored malformed policy command from control server")
            return

        try:
            if command_type == "APPLY_POLICY":
                policy_hash = self._enforcer.apply(payload)
                if self._monitor is not None and session_id:
                    self._monitor.activate(session_id, payload)
            elif command_type == "RESTORE_BASELINE":
                self._enforcer.restore()
                if self._monitor is not None:
                    self._monitor.deactivate()
                policy_hash = None
            else:
                raise ValueError(f"unsupported command type: {command_type}")
        except (OSError, ValueError) as exc:
            error = str(exc)[:500]
            self._client.acknowledge_command(
                command_id,
                success=False,
                error=error,
                actor=self._agent_id,
            )
            self._log(f"Policy command {command_id} failed: {error}")
            return

        self._client.acknowledge_command(
            command_id,
            success=True,
            policy_hash=policy_hash,
            actor=self._agent_id,
        )
        self._log(f"Policy command {command_id} applied successfully")
