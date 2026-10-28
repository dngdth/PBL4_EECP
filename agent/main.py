from __future__ import annotations

import argparse

from agent.client.main import build_client_runtime
from agent.config import (
    AGENT_VERSION,
    POLICY_MODE,
    POLICY_STATE_PATH,
    load_agent_id,
)
from agent.service.runtime import AgentServiceRuntime


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="EECP in-process compatibility runner")
    parser.add_argument(
        "--in-process-compat",
        action="store_true",
        help="explicitly run Client and privileged Service code in one process",
    )
    args = parser.parse_args(argv)
    if not args.in_process_compat:
        raise SystemExit(
            "Production uses separate processes. Run 'python -m agent.service.main' and "
            "'python -m agent.client.main', or pass --in-process-compat for development."
        )
    try:
        agent_id = load_agent_id()
    except RuntimeError as exc:
        raise SystemExit(f"Configuration error: {exc}") from None

    try:
        service_runtime = AgentServiceRuntime.build(
            policy_mode=POLICY_MODE,
            state_path=POLICY_STATE_PATH,
            service_version=AGENT_VERSION,
        )
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from None
    client_runtime = build_client_runtime(agent_id, service_runtime.execution_service)

    try:
        client_runtime.run()
    except KeyboardInterrupt:
        print(f"Stopped agent {agent_id}")


if __name__ == "__main__":
    main()
