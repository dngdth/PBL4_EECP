from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

from agent.config import (
    AGENT_VERSION,
    IPC_MAX_MESSAGE_BYTES,
    IPC_PIPE_NAME,
    POLICY_MODE,
    POLICY_STATE_PATH,
    SERVICE_MAINTENANCE_INTERVAL_SECONDS,
)
from agent.ipc.named_pipe_server import NamedPipeServer
from agent.ipc.protocol import ServiceProtocolHandler
from agent.service.runtime import AgentServiceRuntime


def build_service_runtime() -> AgentServiceRuntime:
    return AgentServiceRuntime.build(
        policy_mode=POLICY_MODE,
        state_path=POLICY_STATE_PATH,
        service_version=AGENT_VERSION,
        server=NamedPipeServer(IPC_PIPE_NAME, IPC_MAX_MESSAGE_BYTES),
        maintenance_interval_seconds=SERVICE_MAINTENANCE_INTERVAL_SECONDS,
    )


def run_service_forever() -> None:
    runtime = build_service_runtime()
    handler = ServiceProtocolHandler(runtime.execution_service.handle, AGENT_VERSION)
    runtime.start(handler)
    print(f"component=agent-service status=ready transport=named-pipe pipe={IPC_PIPE_NAME}")
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
        choices=("install", "remove", "start", "stop", "debug"),
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
            )
            print(
                f"component=agent-service status=config-valid "
                f"transport=named-pipe pipe={IPC_PIPE_NAME}"
            )
            return
        run_service_forever()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Service startup error: {exc}") from None


if __name__ == "__main__":
    main()
