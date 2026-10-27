from __future__ import annotations

import argparse
from collections.abc import Sequence

from agent.application.policy_commands import PolicyCommandProcessor
from agent.application.privileged_execution import PrivilegedExecutionPort
from agent.client.infrastructure.event_spool import AgentEventSpool
from agent.client.infrastructure.gateway_control_client import GatewayControlClient
from agent.client.infrastructure.named_pipe_executor import NamedPipePrivilegedExecutor
from agent.client.runtime import AgentClientRuntime
from agent.client.sensors.dns_audit_sensor import DnsAuditSensor
from agent.client.sensors.process_sensor import ProcessSensor
from agent.client.sensors.security_event_sensor import SecurityEventSensor
from agent.client.sensors.service_health_sensor import ServiceHealthSensor
from agent.client.sensors.suite import SensorSuite
from agent.config import (
    AGENT_VERSION,
    EVENT_SPOOL_PATH,
    GATEWAY_ALLOW_PLAINTEXT_WS,
    GATEWAY_CA_FILE,
    GATEWAY_RECONNECT_INITIAL_SECONDS,
    GATEWAY_RECONNECT_MAX_SECONDS,
    GATEWAY_URL,
    HEARTBEAT_INTERVAL_SECONDS,
    IPC_CONNECT_TIMEOUT_SECONDS,
    IPC_MAX_MESSAGE_BYTES,
    IPC_PIPE_NAME,
    IPC_REQUEST_TIMEOUT_SECONDS,
    POLICY_MODE,
    REQUEST_TIMEOUT_SECONDS,
    SERVER_URL,
    SERVICE_HEALTH_INTERVAL_SECONDS,
    load_agent_gateway_token,
    load_agent_id,
    load_local_security_event_token,
)
from agent.infrastructure.control_server import AgentClient
from agent.infrastructure.system_identity import collect_identity
from agent.infrastructure.violation_monitor import BlockedDomainMonitor
from agent.ipc.named_pipe_client import NamedPipeClient, NamedPipeClientConfig
from contracts.v2 import EventType


def build_client_runtime(
    agent_id: str,
    privileged_executor: PrivilegedExecutionPort,
    control_client=None,
    security_event_transport=None,
) -> AgentClientRuntime:
    identity_endpoint = SERVER_URL if control_client is not None else GATEWAY_URL
    identity = collect_identity(agent_id, AGENT_VERSION, identity_endpoint)
    backend = control_client or GatewayControlClient(
        GATEWAY_URL,
        load_agent_gateway_token(),
        AGENT_VERSION,
        event_spool=AgentEventSpool(EVENT_SPOOL_PATH),
        ca_file=GATEWAY_CA_FILE,
        allow_plaintext_ws=GATEWAY_ALLOW_PLAINTEXT_WS,
        reconnect_initial_seconds=GATEWAY_RECONNECT_INITIAL_SECONDS,
        reconnect_max_seconds=GATEWAY_RECONNECT_MAX_SECONDS,
    )
    monitor = BlockedDomainMonitor(
        lambda session_id, destination: backend.report_policy_violation(
            session_id, identity.agent_id, destination
        )
    )
    monitor.start()
    process_sensor = ProcessSensor(
        lambda session_id, process_name: backend.report_event(
            session_id,
            identity.agent_id,
            EventType.FORBIDDEN_PROCESS_DETECTED,
            {
                "severity": "WARNING",
                "category": "FORBIDDEN_PROCESS",
                "action": "DETECTED",
                "process_name": process_name,
            },
        )
    )
    health_sensor = ServiceHealthSensor(
        privileged_executor,
        lambda session_id, state: backend.report_event(
            session_id,
            identity.agent_id,
            EventType.SERVICE_HEALTH,
            {
                "severity": "WARNING" if state == "UNAVAILABLE" else "INFO",
                "category": "SERVICE_HEALTH",
                "action": state,
            },
        ),
        lambda session_id, expected, actual: backend.report_event(
            session_id,
            identity.agent_id,
            EventType.POLICY_INTEGRITY,
            {
                "severity": "WARNING",
                "category": "POLICY_INTEGRITY",
                "action": "MISMATCH",
                "expected_hash": expected,
                "actual_hash": actual,
            },
        ),
        interval_seconds=SERVICE_HEALTH_INTERVAL_SECONDS,
    )
    polling_sensors = [health_sensor]
    if POLICY_MODE == "audit":
        polling_sensors.append(process_sensor)
        polling_sensors.insert(
            0,
            DnsAuditSensor(
                lambda session_id, destination: backend.report_event(
                    session_id,
                    identity.agent_id,
                    EventType.POLICY_VIOLATION,
                    {
                        "severity": "WARNING",
                        "category": "PROHIBITED_WEBSITE",
                        "action": "OBSERVED",
                        "destination": destination,
                        "enforcement_mode": "audit",
                        "source": "windows-dns-cache",
                    },
                )
            ),
        )
    elif security_event_transport is not None and (
        local_event_token := load_local_security_event_token()
    ):
        polling_sensors.insert(
            0,
            SecurityEventSensor(
                security_event_transport,
                local_event_token,
                lambda session_id, event_type, payload, **event_identity: backend.report_event(
                    session_id,
                    identity.agent_id,
                    event_type,
                    payload,
                    **event_identity,
                ),
            ),
        )
    else:
        # Compatibility fallback when the protected Service journal channel is not configured.
        polling_sensors.insert(0, process_sensor)
    sensors = SensorSuite(
        tuple(polling_sensors),
        health_sensor=health_sensor,
        health_update=getattr(backend, "set_service_health", None),
    )
    processor = PolicyCommandProcessor(
        backend,
        identity.agent_id,
        privileged_executor,
        monitor=monitor,
    )
    return AgentClientRuntime(
        backend,
        identity,
        processor,
        monitor,
        HEARTBEAT_INTERVAL_SECONDS,
        sensors=sensors,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="EECP non-privileged Agent Client"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the standalone client entrypoint without starting networking",
    )
    parser.add_argument(
        "--direct-backend-compat",
        action="store_true",
        help="development-only compatibility mode using direct Backend HTTP",
    )
    args = parser.parse_args(argv)
    if args.check:
        print(
            f"component=agent-client control_transport=gateway-ws "
            f"privileged_transport=named-pipe pipe={IPC_PIPE_NAME} "
            f"policy_mode={POLICY_MODE}"
        )
        return
    try:
        agent_id = load_agent_id()
    except RuntimeError as exc:
        raise SystemExit(f"Configuration error: {exc}") from None
    transport = NamedPipeClient(
        NamedPipeClientConfig(
            pipe_name=IPC_PIPE_NAME,
            connect_timeout_seconds=IPC_CONNECT_TIMEOUT_SECONDS,
            request_timeout_seconds=IPC_REQUEST_TIMEOUT_SECONDS,
            max_message_bytes=IPC_MAX_MESSAGE_BYTES,
        )
    )
    control_client = (
        AgentClient(SERVER_URL, REQUEST_TIMEOUT_SECONDS)
        if args.direct_backend_compat
        else None
    )
    try:
        print(
            f"component=agent-client status=starting agent_id={agent_id} "
            f"policy_mode={POLICY_MODE} "
            f"control_transport={'direct-http' if args.direct_backend_compat else 'gateway-ws'}"
        )
        runtime = build_client_runtime(
            agent_id,
            NamedPipePrivilegedExecutor(transport, AGENT_VERSION),
            control_client,
            transport,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"Configuration error: {exc}") from None
    try:
        runtime.run()
    except KeyboardInterrupt:
        print(f"Stopped agent client {agent_id}")


if __name__ == "__main__":
    main()
