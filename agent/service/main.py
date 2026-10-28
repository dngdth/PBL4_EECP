from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from functools import partial

from agent.config import (
    AGENT_VERSION,
    COMMAND_REPLAY_PATH,
    COMMAND_VERIFICATION_KEY,
    GATEWAY_URL,
    IPC_MAX_MESSAGE_BYTES,
    IPC_PIPE_NAME,
    POLICY_MODE,
    POLICY_STATE_PATH,
    POLICY_VERIFICATION_KEY,
    REQUIRE_AUTHORIZED_COMMANDS,
    REQUIRE_SIGNED_POLICY,
    SECURITY_EVENT_JOURNAL_MAX_ROWS,
    SECURITY_EVENT_JOURNAL_PATH,
    SECURITY_EVENT_RETENTION_DAYS,
    SERVICE_MAINTENANCE_INTERVAL_SECONDS,
    load_agent_id,
    load_local_security_event_token,
)
from agent.infrastructure.policy_enforcement import WindowsPolicyEnforcer
from agent.infrastructure.windows.firewall_enforcer import (
    WindowsFirewallEnforcer,
    resolve_gateway_addresses,
)
from agent.ipc.named_pipe_server import NamedPipeServer
from agent.ipc.protocol import ServiceProtocolHandler
from agent.ipc.security_events import SecurityEventIpcHandler
from agent.service.runtime import AgentServiceRuntime
from agent.service.security_event_journal import (
    SecurityEventJournal,
    protect_service_data_directory,
)


def build_service_runtime() -> AgentServiceRuntime:
    protect_service_data_directory(SECURITY_EVENT_JOURNAL_PATH.parent)
    security_journal = SecurityEventJournal(
        SECURITY_EVENT_JOURNAL_PATH,
        max_rows=SECURITY_EVENT_JOURNAL_MAX_ROWS,
        retention_days=SECURITY_EVENT_RETENTION_DAYS,
    )
    windows_factory = WindowsPolicyEnforcer
    if POLICY_MODE == "enforce":
        protected_endpoints = resolve_gateway_addresses(GATEWAY_URL)
        windows_factory = partial(
            WindowsPolicyEnforcer,
            firewall=WindowsFirewallEnforcer(protected_endpoints),
        )
    return AgentServiceRuntime.build(
        policy_mode=POLICY_MODE,
        state_path=POLICY_STATE_PATH,
        service_version=AGENT_VERSION,
        server=NamedPipeServer(IPC_PIPE_NAME, IPC_MAX_MESSAGE_BYTES),
        maintenance_interval_seconds=SERVICE_MAINTENANCE_INTERVAL_SECONDS,
        windows_factory=windows_factory,
        policy_verification_key=POLICY_VERIFICATION_KEY,
        require_signed_policy=REQUIRE_SIGNED_POLICY,
        command_verification_key=COMMAND_VERIFICATION_KEY,
        require_authorized_commands=REQUIRE_AUTHORIZED_COMMANDS,
        command_target_id=load_agent_id() if REQUIRE_AUTHORIZED_COMMANDS else "",
        replay_path=COMMAND_REPLAY_PATH,
        security_journal=security_journal,
    )


def build_protocol_handler(runtime: AgentServiceRuntime) -> ServiceProtocolHandler:
    local_handler = None
    local_event_token = load_local_security_event_token()
    if runtime.security_journal is not None and local_event_token:
        local_handler = SecurityEventIpcHandler(
            runtime.security_journal, local_event_token, load_agent_id()
        ).try_handle
    return ServiceProtocolHandler(
        runtime.execution_service.handle,
        AGENT_VERSION,
        local_handler=local_handler,
    )


def run_service_forever() -> None:
    runtime = build_service_runtime()
    handler = build_protocol_handler(runtime)
    runtime.start(handler)
    print(
        f"component=agent-service status=ready transport=named-pipe "
        f"pipe={IPC_PIPE_NAME} policy_mode={POLICY_MODE}"
    )
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
        print("component=agent-service status=stopped")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="EECP privileged Agent Service")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration without opening a pipe or changing the OS",
    )
    parser.add_argument(
        "--scm",
        choices=("install", "remove", "start", "stop", "restart", "debug"),
        help="manage the optional pywin32 Windows Service wrapper",
    )
    args = parser.parse_args(argv)
    if args.scm:
        from agent.service.windows_service import run_scm_command

        run_scm_command(args.scm)
        return
    try:
        if args.check:
            AgentServiceRuntime.build(
                policy_mode=POLICY_MODE,
                state_path=POLICY_STATE_PATH,
                service_version=AGENT_VERSION,
                policy_verification_key=POLICY_VERIFICATION_KEY,
                require_signed_policy=REQUIRE_SIGNED_POLICY,
                command_verification_key=COMMAND_VERIFICATION_KEY,
                require_authorized_commands=REQUIRE_AUTHORIZED_COMMANDS,
                command_target_id=(
                    load_agent_id() if REQUIRE_AUTHORIZED_COMMANDS else ""
                ),
                replay_path=COMMAND_REPLAY_PATH,
            )
            print(
                f"component=agent-service status=config-valid "
                f"transport=named-pipe pipe={IPC_PIPE_NAME} policy_mode={POLICY_MODE}"
            )
            return
        run_service_forever()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Service startup error: {exc}") from None


if __name__ == "__main__":
    main()
