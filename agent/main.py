from __future__ import annotations

from agent.application.policy_commands import PolicyCommandProcessor
from agent.application.runtime import run_agent
from agent.config import (
    AGENT_VERSION,
    HEARTBEAT_INTERVAL_SECONDS,
    POLICY_MODE,
    POLICY_STATE_PATH,
    REQUEST_TIMEOUT_SECONDS,
    SERVER_URL,
    load_agent_id,
)
from agent.infrastructure.control_server import AgentClient
from agent.infrastructure.policy_enforcement import (
    AuditPolicyEnforcer,
    WindowsPolicyEnforcer,
)
from agent.infrastructure.system_identity import collect_identity
from agent.infrastructure.violation_monitor import BlockedDomainMonitor


def main() -> None:
    try:
        agent_id = load_agent_id()
    except RuntimeError as exc:
        raise SystemExit(f"Configuration error: {exc}") from None

    identity = collect_identity(agent_id, AGENT_VERSION, SERVER_URL)
    client = AgentClient(SERVER_URL, REQUEST_TIMEOUT_SECONDS)
    violation_monitor = BlockedDomainMonitor(
        lambda session_id, destination: client.report_policy_violation(
            session_id, identity.agent_id, destination
        )
    )
    if POLICY_MODE == "audit":
        enforcer = AuditPolicyEnforcer(POLICY_STATE_PATH)
    elif POLICY_MODE == "enforce":
        enforcer = WindowsPolicyEnforcer(POLICY_STATE_PATH)
    else:
        raise SystemExit(
            "Configuration error: EECP_POLICY_MODE must be 'enforce' or 'audit'"
        )
    violation_monitor.start()
    command_processor = PolicyCommandProcessor(
        client,
        identity.agent_id,
        enforcer,
        monitor=violation_monitor,
    )

    def process_control_cycle() -> None:
        command_processor.process_pending()
        active_policy = client.active_policy(identity.agent_id)
        if active_policy is None:
            violation_monitor.deactivate()
        else:
            session_id, policy = active_policy
            violation_monitor.activate(session_id, policy)

    try:
        run_agent(
            client,
            identity,
            HEARTBEAT_INTERVAL_SECONDS,
            process_commands=process_control_cycle,
        )
    except KeyboardInterrupt:
        print(f"Stopped agent {identity.agent_id}")


if __name__ == "__main__":
    main()
