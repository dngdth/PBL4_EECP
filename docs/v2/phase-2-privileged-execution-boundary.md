# Phase 2 — Privileged Execution Boundary

## 1. Goal

Phase 2 introduces a logical application boundary between command orchestration and
privileged OS enforcement. It is a dependency-inversion and testability seam before
the future process/security boundary. All components still run in one Python process.

## 2. Before

```text
PolicyCommandProcessor
        |
        | apply / restore / maintain
        v
PolicyEnforcer implementation
        |
        v
WindowsPolicyEnforcer or AuditPolicyEnforcer
```

`PolicyCommandProcessor` previously declared a local `PolicyEnforcer` protocol whose
methods mirrored the concrete enforcement API. It called `apply(payload)`,
`restore()`, and `maintain()` directly, caught `OSError`/`ValueError` around command
execution, and sent the existing HTTP ACK itself.

## 3. After

```text
                       SAME PROCESS

PolicyCommandProcessor
        |
        | ServiceRequest
        v
PrivilegedExecutionPort
        |
        | ServiceResult
        v
InProcessPrivilegedExecutor
        |
        | unchanged apply / restore / maintain calls
        v
WindowsPolicyEnforcer or AuditPolicyEnforcer
```

`agent/main.py` remains the composition root. It selects the existing audit or
Windows enforcer, wraps it in `InProcessPrivilegedExecutor`, and injects the executor
into `PolicyCommandProcessor`.

## 4. Responsibilities

### PolicyCommandProcessor

- Owns command polling/application flow and the existing HTTP ACK integration.
- Maps the current backend command dictionary to a Protocol v2 `ServiceRequest`.
- Activates or deactivates the existing violation monitor only after successful
  execution.
- Depends only on `PrivilegedExecutionPort`; it does not know Windows enforcement or
  import Agent infrastructure.

### PrivilegedExecutionPort

- Application-layer abstraction for one allowlisted privileged operation.
- Exposes `execute(ServiceRequest) -> ServiceResult` and `maintain() -> None`.
- Contains no transport, persistence, Windows, shell, registry, or filesystem API.

### InProcessPrivilegedExecutor

- Current same-process infrastructure adapter.
- Converts the typed `APPLY_POLICY` request back to the unchanged legacy policy
  dictionary expected by the enforcer.
- Delegates restore and maintain without moving or rewriting their logic.
- Returns structured Protocol v2 execution results.

### WindowsPolicyEnforcer / AuditPolicyEnforcer

- Retain all existing OS/audit behavior and state format.
- `agent/infrastructure/policy_enforcement.py` is unchanged in Phase 2.

## 5. Supported operations

| Operation | In-process behavior |
| --- | --- |
| `APPLY_POLICY` | Validate the typed request, call the existing `apply()`, and return the applied hash. |
| `RESTORE_BASELINE` | Call the existing `restore()` and return success. |
| `HEALTH_CHECK` | Return executor availability without touching the enforcer or adding an endpoint/monitor. |

The allowlist comes from Protocol v2. There is no shell, PowerShell, arbitrary
program, generic file write, or generic registry operation.

## 6. Maintain behavior

`PolicyCommandProcessor.process_pending()` still invokes maintenance once after each
poll/processing cycle. The call now crosses `PrivilegedExecutionPort.maintain()`, and
`InProcessPrivilegedExecutor` immediately delegates to the existing enforcer.

This preserves the current denied-process re-termination loop. It does not reassert
hosts or USB because the existing enforcer does not do so. Phase 4 may move ownership
of this loop into a privileged Windows Service; Phase 2 does not.

## 7. Non-goals

- No Named Pipe client/server or other IPC transport.
- No Windows Service or Agent process split.
- No Local Gateway, Gateway routing, or WebSocket.
- No Windows Firewall/WFP behavior.
- No PostgreSQL, Redis, RBAC, or authentication migration.
- No frontend, Docker, HTTP schema, backend use-case, policy-hash, or enforcer rewrite.

## 8. Migration path

Current Phase 2:

```text
PolicyCommandProcessor
        v
PrivilegedExecutionPort
        v
InProcessPrivilegedExecutor
```

Future phase, not implemented here:

```text
Agent Client / PolicyCommandProcessor
        v
PrivilegedExecutionPort or IPC adapter
        v
NamedPipePrivilegedExecutor
        v
Privileged Agent Service process
```

Because the processor exchanges `ServiceRequest` and `ServiceResult` at the port,
the future adapter can change transport/process placement without teaching the
processor Windows enforcement details.

## 9. Mapping and error handling

The current backend continues sending its existing command JSON. The processor maps:

| Current command | Service request |
| --- | --- |
| `id` | `command_id`; deterministic request identity `req_<command_id>` |
| `type` | allowlisted `operation` |
| `session_id` | `session_id` |
| current policy payload | typed `ApplyPolicyPayload(PolicyEnvelope)` |
| restore payload | typed `RestoreBaselinePayload(NORMAL)` |
| no current correlation ID | `command_id` as compatibility correlation ID |

For valid backend commands, policy rules and the backward-compatible SHA-256 digest
are preserved. The in-process adapter reconstructs the existing
`eecp-policy/v1` dictionary before calling the enforcer.

| Condition | ServiceResult |
| --- | --- |
| Invalid apply policy (`ValueError`) | `FAILED / INVALID_POLICY` |
| Enforcer OS failure (`OSError`) | `FAILED / EXECUTION_FAILED` |
| Returned hash differs from requested hash | `FAILED / POLICY_HASH_MISMATCH` |
| Non-allowlisted operation | `REJECTED / UNSUPPORTED_OPERATION` |

The processor maps success/failure back to the unchanged current ACK body. It still
sends `success`, `policy_hash` when applicable, `error` on failure, and `actor`.

## 10. Tests

- Processor tests use a fake `PrivilegedExecutionPort` and verify operation mapping,
  apply/restore monitor lifecycle, legacy ACK output, failure ACK, and maintenance.
- Executor tests use a recording fake enforcer and verify apply, restore, health,
  maintain, unsupported-operation rejection, known error mapping, and hash mismatch.
- Existing Windows enforcer tests still cover hosts content, `taskkill` invocation,
  USB baseline capture/restore, and audit-to-enforce switching with temporary files
  and a fake process runner.
- Architecture tests enforce that `agent/application` does not import
  `agent.infrastructure`.
- Real elevated Windows enforcement is not run outside an isolated VM.

## 11. Known limitations

- This is still one process and therefore not a security boundary.
- A crash or compromise of the current Agent affects orchestration and enforcement.
- There is no Named Pipe, Windows Service, IPC authentication, ACL, replay cache, or
  separate service lifecycle.
- The current Agent runtime still owns the maintain loop.
- `HEALTH_CHECK` is local executor behavior only; the backend does not send it.
- Idempotency persistence remains unchanged; Phase 2 adds no processed-command store.
- There is no Firewall or Local Gateway implementation.
