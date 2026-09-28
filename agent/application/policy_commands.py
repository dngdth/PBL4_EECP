from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from agent.application.privileged_execution import PrivilegedExecutionPort
from contracts.v2 import (
    AckStatus,
    ApplyPolicyPayload,
    CommandType,
    HealthCheckPayload,
    PolicyEnvelope,
    RestoreBaselinePayload,
    ServiceRequest,
)


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


class PolicyMonitor(Protocol):
    def activate(self, session_id: str, payload: dict[str, Any]) -> None: ...
    def deactivate(self) -> None: ...


class PolicyCommandProcessor:
    def __init__(
        self,
        client: CommandClient,
        agent_id: str,
        privileged_executor: PrivilegedExecutionPort,
        monitor: PolicyMonitor | None = None,
        log: Callable[[str], None] = print,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._client = client
        self._agent_id = agent_id
        self._privileged_executor = privileged_executor
        self._monitor = monitor
        self._log = log
        self._clock = clock

    def process_pending(self) -> None:
        for command in self._client.pending_commands(self._agent_id):
            self._execute(command)

    def _execute(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("id", ""))
        session_id = str(command.get("session_id", ""))
        command_type = command.get("type")
        payload = command.get("payload")
        if not command_id or not isinstance(payload, dict):
            self._log("Ignored malformed policy command from control server")
            return

        try:
            request = self._build_service_request(command_id, session_id, command_type, payload)
            result = self._privileged_executor.execute(request)
            if result.status != AckStatus.SUCCEEDED:
                error = (result.error_message or result.error_code or "execution failed")
                error = str(error)[:500]
                self._client.acknowledge_command(
                    command_id,
                    success=False,
                    error=error,
                    actor=self._agent_id,
                )
                self._log(f"Policy command {command_id} failed: {error}")
                return

            if request.operation == CommandType.APPLY_POLICY:
                policy_hash = result.applied_hash
                if self._monitor is not None and session_id:
                    self._monitor.activate(session_id, payload)
            elif request.operation == CommandType.RESTORE_BASELINE:
                if self._monitor is not None:
                    self._monitor.deactivate()
                policy_hash = None
            else:
                policy_hash = None
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

    def _build_service_request(
        self,
        command_id: str,
        session_id: str,
        command_type: object,
        payload: dict[str, Any],
    ) -> ServiceRequest:
        try:
            operation = CommandType(command_type)
        except ValueError:
            raise ValueError(f"unsupported command type: {command_type}") from None
        correlation_id = command_id
        request_id = f"req_{command_id}"

        if operation == CommandType.APPLY_POLICY:
            policy_hash = payload.get("policy_hash")
            policy_id = payload.get("profile")
            policy_version = payload.get("version")
            rules = payload.get("rules")
            if not isinstance(policy_hash, str):
                raise ValueError("policy_hash must be a string")
            if not isinstance(policy_id, str):
                raise ValueError("policy profile must be a string")
            if not isinstance(policy_version, int):
                raise ValueError("policy version must be an integer")
            if not isinstance(rules, dict):
                raise ValueError("policy rules must be an object")
            policy = PolicyEnvelope.from_legacy(
                policy_id=policy_id,
                policy_version=policy_version,
                policy_hash=policy_hash,
                session_id=session_id,
                issued_at=self._clock(),
                rules=rules,
            )
            service_payload = ApplyPolicyPayload(policy=policy)
        elif operation == CommandType.RESTORE_BASELINE:
            value = payload.get("policy_hash")
            policy_hash = value if isinstance(value, str) else None
            service_payload = RestoreBaselinePayload(baseline="NORMAL")
        else:
            policy_hash = None
            nonce = payload.get("nonce")
            service_payload = HealthCheckPayload(
                nonce=nonce if isinstance(nonce, str) else None
            )

        return ServiceRequest(
            protocol_version=2,
            request_id=request_id,
            command_id=command_id,
            operation=operation,
            session_id=session_id,
            policy_hash=policy_hash,
            payload=service_payload,
            correlation_id=correlation_id,
        )
