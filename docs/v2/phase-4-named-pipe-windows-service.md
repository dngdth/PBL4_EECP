# Phase 4 — Named Pipe IPC + Windows Service

## 1. Goal

Phase 4 creates a real local process and privilege boundary. The normal-user Agent
Client communicates with a separately launched privileged Agent Service. Only the
Service owns policy enforcement and maintenance.

## 2. Architecture before

Phase 3 separated code responsibilities, but `agent.main` still passed an
`ExecutionService` object directly into the Client in one process:

```text
Agent Client -> PrivilegedExecutionPort -> ExecutionService -> policy enforcer
```

`PolicyCommandProcessor` also triggered maintenance after each backend poll.

## 3. Architecture after

```text
PROCESS 1 — normal user
Agent Client
  -> backend HTTP (unchanged)
  -> PolicyCommandProcessor
  -> NamedPipePrivilegedExecutor
  -> \\.\pipe\eecp-agent-v2

PROCESS 2 — privileged
NamedPipeServer
  -> Protocol v2 validation
  -> ExecutionService
  -> InProcessPrivilegedExecutor
  -> WindowsPolicyEnforcer / AuditPolicyEnforcer
  -> Service-owned maintenance loop
```

No Service HTTP, TCP, or WebSocket listener exists.

## 4. Process boundary

Production entrypoints are separate:

```powershell
python -m agent.service.main
python -m agent.client.main
```

The Service process must be started with the rights needed for hosts, HKLM USBSTOR,
and process enforcement. The Client does not import or construct an enforcer and is
intended to run as a normal user.

The old same-process path remains only behind an explicit development invocation:

```powershell
python -m agent.main --in-process-compat
```

## 5. Named Pipe

- Name: `\\.\pipe\eecp-agent-v2`
- Mode: byte-mode, local-only Windows Named Pipe
- Framing: 4-byte unsigned big-endian payload length followed by UTF-8 JSON
- Maximum JSON payload: 1 MiB
- Connect timeout: 2 seconds
- Whole request/response timeout: 10 seconds
- Connection model: one request and one response per connection

Reads and writes loop until the declared frame has been transferred. Zero-length,
oversized, truncated, and invalid frames are rejected without unbounded buffering.
The Client opens a fresh connection for each request. A broken connection returns a
structured execution failure; a later request reconnects without an infinite retry.

## 6. ACL

The server supplies an explicit protected DACL through this SDDL:

```text
D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;AU)
```

It grants full access to LocalSystem and Builtin Administrators, and read/write to
Authenticated Users. `PIPE_REJECT_REMOTE_CLIENTS` prevents remote pipe clients.
World/Everyone and Anonymous are not granted access.

Authenticated Users is a deliberate transitional client-principal strategy because
the current deployment has no enrollment-provisioned user SID or dedicated local
Agent group. A hardened installer should provision a dedicated local group/SID and
replace the `AU` ACE. The Service still validates every request; ACL membership is
not treated as authorization for arbitrary privileged behavior.

## 7. Request validation

The pipe transports the existing strict `ServiceRequest` and `ServiceResult` Protocol
v2 models. There is no duplicate IPC schema. Before execution the Service checks:

- valid JSON and strict Protocol v2 schema (unknown fields are rejected);
- protocol version, required IDs, operation/payload pairing, and session pairing;
- the fixed operation allowlist (`APPLY_POLICY`, `RESTORE_BASELINE`, `HEALTH_CHECK`);
- canonical policy hash and request/policy hash equality;
- policy expiry when `expires_at` is present;
- replay/idempotency state.

The current `ServiceRequest` schema does not carry a command deadline, so the Service
cannot independently re-check the backend `Command.deadline`. Policy `expires_at` is
checked at the privileged boundary; command deadline validation remains in the
upstream command-delivery contract/path until a future compatible request-contract
revision.

`RESTORE_BASELINE` only calls the existing EECP restore behavior. The pipe protocol
contains no shell command, arbitrary method, registry path, or filesystem path.
Malformed requests receive a structured Protocol v2 error when enough identity fields
can be recovered; malformed framing is closed because it is not a valid message.

Policy signatures remain optional contract data. Cryptographic signature verification
has not been implemented, and Phase 4 does not pretend otherwise.

## 8. Replay protection

`ExecutionService` owns a bounded 1,024-entry in-memory LRU cache keyed by
`command_id`. An identical retry returns the cached `ServiceResult` and does not
execute again. Reusing a command ID with different request content returns
`DUPLICATE_COMMAND`.

The cache is intentionally not Redis/database-backed and is lost when the Service
restarts. Persistent idempotency is a later hardening item.

## 9. Client reconnect

Each `NamedPipePrivilegedExecutor.execute()` call creates a bounded connection. A
missing, stopped, restarted, or broken Service maps to a failed `ServiceResult`, so the
Agent loop can ACK failure without crashing. The next command makes a new connection.
There is no unbounded blocking or automatic infinite command retry.

## 10. Service lifecycle

`AgentServiceRuntime` exposes `STARTING`, `READY`, `STOPPING`, and `STOPPED`. Start
waits until the pipe server has created its first instance, then starts a maintenance
thread. Stop closes/wakes the pipe, signals maintenance, joins both lifecycles, and
reaches `STOPPED`.

`HEALTH_CHECK` reports success/aliveness, service version, executor availability, and
the in-process active policy hash through existing `ServiceResult` fields. It does not
expose environment, filesystem, process, token, or credential data. Maintenance
errors are retained internally by the runtime and are not expanded into a new schema.

## 11. Maintain ownership

Maintenance is Service-owned. `PolicyCommandProcessor` no longer calls `maintain()`,
and the Client-facing `PrivilegedExecutionPort` no longer exposes it. The Service
thread calls `InProcessPrivilegedExecutor.maintain()` at a configured interval even
when no Client is connected.

Active policy hash and session ID are held by `ExecutionService` for the lifetime of
the Service process. The existing enforcer state file continues to support enforcement
behavior, but the service-level session state is not reconstructed after a restart.

## 12. Windows Service wrapper

`agent.service.windows_service` provides a pywin32 SCM wrapper with stable names:

- internal name: `EECPAgentService`
- display name: `EECP Privileged Agent Service`
- intended account: LocalSystem

Manual isolated-VM commands are:

```powershell
python -m agent.service.main --scm install
python -m agent.service.main --scm start
python -m agent.service.main --scm stop
python -m agent.service.main --scm remove
python -m agent.service.main --scm debug
```

These commands require pywin32 on the Windows target and are never run by unit tests.
LocalSystem is highly privileged; the narrow local allowlisted interface and explicit
ACL are therefore mandatory.

## 13. Security constraints

- No arbitrary shell, PowerShell, CMD, subprocess, dynamic import, `eval`, or `exec`
  is reachable from an IPC payload.
- No arbitrary filesystem or registry path is accepted from a request.
- No network listener is created by the Service.
- Local requests are schema-, semantic-, expiry-, and replay-validated.
- The existing enforcement implementation is unchanged.
- Logs do not include full policies, credentials, signatures, or security tokens.

## 14. Tests

Automated tests cover framing, partial reads/writes, size bounds, malformed JSON,
strict schemas, unsupported versions/operations, missing IDs, hash mismatch, expiry,
replay, conflicting duplicates, active-policy health, structured disconnects, bounded
timeouts, reconnect, ACL policy, lifecycle shutdown, and maintenance independence.
Architecture tests prohibit privileged implementation imports from Client code,
backend networking from Service code, and maintenance calls from
`PolicyCommandProcessor`.

## 15. Windows VM tests

A real non-elevated Windows Named Pipe integration test runs a server, performs a
request, creates a new Client instance, reconnects, and performs another request. A
real missing-pipe timeout is also tested.

SCM install/start/stop as LocalSystem, real elevated hosts/HKLM/process enforcement,
and killing a real Client process while observing enforced OS state require an isolated
elevated Windows VM. They remain blocked in the development environment and are not
reported as passed.

## 16. Known limitations

- Replay state is in memory and is lost on Service restart.
- Active session/policy metadata is not reconstructed into `ExecutionService` after a
  Service restart, although the existing enforcer state file remains unchanged.
- The ACL uses Authenticated Users until deployment provisions a dedicated principal.
- Signature verification is not implemented.
- `ServiceRequest` has no command deadline field; the Service independently validates
  policy expiry but cannot repeat the upstream command-deadline check.
- SCM and real elevated enforcement require manual isolated-VM verification.
- The pywin32 SCM dependency is target-optional and is not added to cross-platform CI.

## 17. Phase 5 readiness

Phase 5 may change only backend networking topology:

```text
Current: Agent Client <-> Backend
Next:    Agent Client <-> Local Gateway <-> Backend
```

The Phase 4 boundary remains unchanged:

```text
Agent Client -> Named Pipe -> Privileged Agent Service
```

Phase 4 does not implement the Local Gateway, Windows Firewall/WFP, PostgreSQL,
Redis, RBAC, frontend changes, offline buffering, PKI, or remote shell.
