from __future__ import annotations

import json
import threading
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from agent.config import IPC_REPLAY_CACHE_SIZE
from agent.infrastructure.inprocess_executor import (
    InProcessPrivilegedExecutor,
    PolicyEnforcer,
)
from agent.infrastructure.policy_enforcement import (
    AuditPolicyEnforcer,
    WindowsPolicyEnforcer,
)
from agent.service.application.execution_service import ExecutionService

EnforcerFactory = Callable[[Path], PolicyEnforcer]


class RequestServer(Protocol):
    def start(self, handler: Callable[[bytes], bytes]) -> None: ...

    def stop(self) -> None: ...


class ServiceLifecycle(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class AgentServiceRuntime:
    """Own privileged execution, local IPC, and the maintenance lifecycle."""

    def __init__(
        self,
        enforcer: PolicyEnforcer,
        privileged_executor: InProcessPrivilegedExecutor,
        execution_service: ExecutionService,
        *,
        server: RequestServer | None = None,
        maintenance_interval_seconds: float = 5.0,
    ):
        if maintenance_interval_seconds <= 0:
            raise ValueError("maintenance_interval_seconds must be positive")
        self.enforcer = enforcer
        self.privileged_executor = privileged_executor
        self.execution_service = execution_service
        self._server = server
        self._maintenance_interval_seconds = maintenance_interval_seconds
        self._stop_event = threading.Event()
        self._maintenance_thread: threading.Thread | None = None
        self._lifecycle = ServiceLifecycle.STOPPED
        self._lifecycle_lock = threading.Lock()
        self._maintenance_error: str | None = None

    @classmethod
    def build(
        cls,
        *,
        policy_mode: str,
        state_path: Path,
        service_version: str,
        server: RequestServer | None = None,
        maintenance_interval_seconds: float = 5.0,
        replay_capacity: int = IPC_REPLAY_CACHE_SIZE,
        audit_factory: EnforcerFactory = AuditPolicyEnforcer,
        windows_factory: EnforcerFactory = WindowsPolicyEnforcer,
        policy_verification_key: str = "",
        require_signed_policy: bool = False,
        command_verification_key: str = "",
        require_authorized_commands: bool = False,
        command_target_id: str = "",
        replay_path: Path | None = None,
    ) -> AgentServiceRuntime:
        if policy_mode == "audit":
            enforcer = audit_factory(state_path)
        elif policy_mode == "enforce":
            enforcer = windows_factory(state_path)
        else:
            raise ValueError("EECP_POLICY_MODE must be 'enforce' or 'audit'")
        privileged_executor = InProcessPrivilegedExecutor(enforcer, service_version)
        active_policy_hash, active_session_id = _load_active_policy_state(state_path)
        execution_service = ExecutionService(
            privileged_executor,
            service_version=service_version,
            replay_capacity=replay_capacity,
            policy_verification_key=policy_verification_key,
            require_signed_policy=require_signed_policy,
            command_verification_key=command_verification_key,
            require_authorized_commands=require_authorized_commands,
            command_target_id=command_target_id,
            replay_path=replay_path,
            active_policy_hash=active_policy_hash,
            active_session_id=active_session_id,
        )
        return cls(
            enforcer,
            privileged_executor,
            execution_service,
            server=server,
            maintenance_interval_seconds=maintenance_interval_seconds,
        )

    @property
    def lifecycle(self) -> ServiceLifecycle:
        with self._lifecycle_lock:
            return self._lifecycle

    @property
    def maintenance_error(self) -> str | None:
        return self._maintenance_error

    def start(self, handler: Callable[[bytes], bytes] | None = None) -> None:
        with self._lifecycle_lock:
            if self._lifecycle != ServiceLifecycle.STOPPED:
                raise RuntimeError("service is already running")
            self._lifecycle = ServiceLifecycle.STARTING
        self._stop_event.clear()
        try:
            if self._server is not None:
                if handler is None:
                    raise ValueError("an IPC request handler is required")
                self._server.start(handler)
            self._maintenance_thread = threading.Thread(
                target=self._maintenance_loop,
                name="eecp-service-maintenance",
                daemon=True,
            )
            self._maintenance_thread.start()
        except Exception:
            if self._server is not None:
                self._server.stop()
            with self._lifecycle_lock:
                self._lifecycle = ServiceLifecycle.STOPPED
            raise
        with self._lifecycle_lock:
            self._lifecycle = ServiceLifecycle.READY

    def stop(self) -> None:
        with self._lifecycle_lock:
            if self._lifecycle == ServiceLifecycle.STOPPED:
                return
            self._lifecycle = ServiceLifecycle.STOPPING
        self._stop_event.set()
        if self._server is not None:
            self._server.stop()
        if self._maintenance_thread is not None:
            self._maintenance_thread.join(
                timeout=max(1.0, self._maintenance_interval_seconds + 1.0)
            )
            self._maintenance_thread = None
        with self._lifecycle_lock:
            self._lifecycle = ServiceLifecycle.STOPPED

    def maintain_once(self) -> None:
        self.privileged_executor.maintain()

    def _maintenance_loop(self) -> None:
        while not self._stop_event.wait(self._maintenance_interval_seconds):
            try:
                self.maintain_once()
                self._maintenance_error = None
            except (OSError, ValueError) as exc:
                self._maintenance_error = str(exc)[:500]


def _load_active_policy_state(path: Path) -> tuple[str | None, str | None]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, json.JSONDecodeError) as exc:
        raise OSError(f"cannot read EECP policy state: {exc}") from exc
    if not isinstance(state, dict):
        raise OSError("EECP policy state is invalid")
    policy_hash = state.get("policy_hash")
    session_id = state.get("session_id")
    if not isinstance(policy_hash, str) or not isinstance(session_id, str):
        raise OSError("EECP active policy identity is invalid")
    return policy_hash, session_id
