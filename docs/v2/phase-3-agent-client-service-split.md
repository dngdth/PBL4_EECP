# Phase 3 — Agent Client / Agent Service Split

## 1. Goal

Phase 3 separates Agent Client responsibilities from privileged Agent Service
responsibilities in the code and lifecycle structure. This prepares the existing
`PrivilegedExecutionPort` and Protocol v2 `ServiceRequest`/`ServiceResult` boundary
for a future IPC adapter without changing backend networking or OS enforcement.

This is a code/process responsibility split ready for the IPC phase. It is not yet a
real privilege security boundary.

## 2. Before

```text
Single Agent process / composition root
    +-- backend AgentClient
    +-- BlockedDomainMonitor
    +-- PolicyCommandProcessor
    +-- InProcessPrivilegedExecutor
    +-- WindowsPolicyEnforcer or AuditPolicyEnforcer
```

Although Phase 2 introduced a logical execution port, one composition root still
constructed networking, monitoring, processing, and enforcement together.

## 3. After

```text
+---------------- AGENT CLIENT ----------------+
| backend communication                        |
| registration / heartbeat / polling / ACK     |
| monitoring and violation telemetry           |
| PolicyCommandProcessor                       |
|        |                                     |
|        v                                     |
| PrivilegedExecutionPort                      |
+----------------------------------------------+

          PRODUCTION IPC NOT IMPLEMENTED

+---------------- AGENT SERVICE ---------------+
| ExecutionService                             |
|        |                                     |
|        v                                     |
| InProcessPrivilegedExecutor                  |
|        |                                     |
|        v                                     |
| WindowsPolicyEnforcer / AuditPolicyEnforcer  |
+----------------------------------------------+
```

The compatibility entrypoint still connects both sides inside one process. Separate
entrypoints now exist and can validate their own composition safely, but they do not
communicate with each other in production until Phase 4 supplies IPC.

## 4. Client responsibilities

The `agent.client` package owns:

- collection of workstation identity;
- construction and use of the existing HTTP `AgentClient`;
- register, heartbeat, command polling, current ACK, and violation telemetry;
- `BlockedDomainMonitor` startup and active-policy monitoring;
- `PolicyCommandProcessor` construction;
- mapping backend commands to `ServiceRequest` through the existing processor;
- consumption of a supplied `PrivilegedExecutionPort`.

Client code does not import `WindowsPolicyEnforcer`, `AuditPolicyEnforcer`,
`InProcessPrivilegedExecutor`, or `agent.service`. It does not modify hosts, HKLM,
USB state, or processes directly.

## 5. Service responsibilities

The `agent.service` package owns:

- audit/enforce mode selection for the privileged side;
- construction and ownership of `WindowsPolicyEnforcer` or `AuditPolicyEnforcer`;
- construction of `InProcessPrivilegedExecutor`;
- transport-neutral `ExecutionService.handle(ServiceRequest) -> ServiceResult`;
- explicit `AgentServiceRuntime.maintain_once()` lifecycle capability;
- safe service composition validation without applying a policy.

The service does not construct a backend client, register, heartbeat, poll backend
commands, send HTTP ACK/telemetry, know the dashboard, or expose a network listener.

## 6. Dependency boundaries

- `agent/application` cannot import `agent.infrastructure` or `agent.service`.
- `agent/client` may use backend/monitor adapters but cannot import privileged
  enforcement, the in-process privileged adapter, or service internals.
- `agent/service/application` depends only on the application port and Protocol v2;
  it cannot import backend networking, FastAPI, sockets, HTTP servers, or Uvicorn.
- `agent/service/runtime.py` is the service-side composition root and may construct
  infrastructure enforcement adapters.
- Shared `contracts/v2` remains independent of Agent and backend runtime layers.

These rules are enforced by architecture tests based on Python imports rather than
absolute workstation paths.

## 7. Entrypoints

### Agent Client

```powershell
python -m agent.client.main --help
python -m agent.client.main --check
```

`--check` performs no networking. Running the standalone client without an injected
IPC implementation exits with an explicit message instead of pretending a production
Client-Service connection exists.

### Agent Service

```powershell
python -m agent.service.main --help
python -m agent.service.main --check
```

The service check constructs ownership objects only. It does not apply/restore a
policy, call `maintain()`, mutate Windows state, or open an HTTP/TCP/WebSocket server.

### Compatibility entrypoint

```powershell
python -m agent.main
```

This preserves current CLI behavior. It builds `AgentServiceRuntime`, passes its
`ExecutionService` to `AgentClientRuntime`, and runs both logical sides in the same
process. This is explicitly `IN_PROCESS_COMPATIBILITY_MODE`, not the target secure
deployment.

## 8. ServiceRequest / ServiceResult flow

In compatibility mode:

```text
Backend HTTP command
        v
AgentClientRuntime / PolicyCommandProcessor
        v
ServiceRequest
        v
ExecutionService
        v
InProcessPrivilegedExecutor
        v
existing enforcer
        v
ServiceResult
        v
PolicyCommandProcessor
        v
existing backend HTTP ACK
```

`ExecutionService` does not duplicate operation, validation, error, or ACK logic. It
delegates the request to the configured `PrivilegedExecutionPort` and preserves the
returned result.

## 9. Maintain ownership

`AgentServiceRuntime` now exposes `maintain_once()` and owns the enforcer/executor
that perform maintenance. The service-side `ExecutionService.maintain()` delegates to
the executor and unchanged enforcer.

Transitional limitation: in in-process compatibility mode,
`PolicyCommandProcessor.process_pending()` still triggers `maintain()` after each
client poll cycle through the port. The standalone service entrypoint does not start a
maintenance thread or watchdog. Therefore Phase 3 does not prove that maintenance
continues if the Client dies. Phase 4 must move the recurring lifecycle completely
into the actual privileged Service process.

## 10. Security status

**NOT YET A REAL PRIVILEGE SECURITY BOUNDARY.** Phase 3 has no Named Pipe, pipe ACL,
Windows Service, SCM installation, LocalSystem isolation, service identity, replay
cache, or authenticated IPC. The compatibility process has the same privileges as
before.

No claim is made that killing the Client leaves enforcement maintenance running.

## 11. Non-goals

- No Named Pipe client/server or `NamedPipePrivilegedExecutor`.
- No Windows Service, SCM installer, LocalSystem account, ACL, or recovery policy.
- No localhost HTTP, TCP, WebSocket, or other temporary service API.
- No Local Gateway or Agent-to-Gateway transport.
- No Windows Firewall/WFP enforcement.
- No PostgreSQL, Redis, RBAC, credential, frontend, or Docker migration.
- No change to backend HTTP schemas, policy hash, state file, hosts, USB, process,
  restore, or enforcement behavior.

## 12. Tests

- ExecutionService delegates all three allowlisted operations and preserves success,
  failure, and rejection results.
- Client runtime tests cover registration, heartbeat, command-cycle invocation,
  active-policy monitoring, and construction with a fake privileged port.
- Service runtime tests cover audit/enforce selection, enforcer ownership,
  `ExecutionService` exposure, maintenance delegation, and invalid configuration.
- Architecture tests prevent Client privileged imports and Service application
  backend/network imports.
- Both entrypoints pass `--help` and safe `--check` smoke tests.
- Existing Protocol v2, Phase 2 boundary, backend, Agent, and frontend regressions
  remain part of the full verification suite.

Real elevated Windows enforcement remains blocked outside an isolated VM.

## 13. Known limitations

- Client and Service are still joined only by in-process compatibility composition.
- There is no production IPC implementation.
- The standalone client cannot execute commands until an IPC-backed port exists.
- The standalone service has no request transport and exits after safe initialization.
- Maintenance has a service-owned API but no independent recurring Service loop.
- Existing active policy state is not automatically applied or maintained at Service
  startup; this avoids unsafe auto-execution.
- There is no process identity/privilege isolation or tamper recovery.

## 14. Phase 4 migration

Phase 4 can replace the compatibility connection with:

```text
Agent Client
        v
NamedPipePrivilegedExecutor
        |
        | \\.\pipe\eecp-agent-v2 with explicit ACL
        v
Agent Service process
        v
ExecutionService
        v
InProcessPrivilegedExecutor
        v
WindowsPolicyEnforcer
```

It must also move the recurring maintenance lifecycle fully into the Service and
prove behavior independently of the Client. None of that transport, service, ACL, or
process work is implemented in Phase 3.
