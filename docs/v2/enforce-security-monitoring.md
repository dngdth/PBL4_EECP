# Enforce security monitoring

## Architecture

Security evidence created by the privileged Windows Service is written first to
`%ProgramData%\EECP\security-events.db`. The Service data directory removes inherited
ACLs and grants full control only to LocalSystem and Built-in Administrators.

The normal-user Agent reads bounded batches over the existing local-only Named Pipe
using `LIST_PENDING_SECURITY_SESSIONS` and `READ_SECURITY_EVENTS`. The listing is
metadata-only and every request is bound to the configured Agent identity plus the
machine-local token. This lets a reconnecting Client drain events after Session
FINISHED without reactivating Enforce. After each event has been durably written to
the AgentEventSpool, it sends `ACK_SECURITY_EVENTS`. None of these operations can
execute SQL, access an arbitrary file, run a shell command, or mutate enforcement.
The original event ID and occurrence time are preserved. A Backend permanent reject
is quarantined rather than retried forever or silently deleted.

```text
Windows Service -> Service SQLite journal -> authenticated read/ack IPC
  -> AgentEventSpool -> Gateway durable buffer -> Backend telemetry
  -> scoped Realtime notification -> Dashboard security timeline
```

Provision a machine-local secret for the Windows account that runs the Agent Client
from an elevated PowerShell window:

```powershell
.\scripts\windows\setup-security-event-ipc.ps1 -Action Install -AgentUser "STUDENT01"
```

The script writes a random secret below `%ProgramData%\EECP-Client` and grants read
access only to that Agent user, LocalSystem, and Administrators. An environment value
`EECP_LOCAL_SECURITY_EVENT_TOKEN` can be used by development processes instead. If
neither source is configured, enforcement remains active and the Client retains its
legacy process sensor fallback, but Service journal delivery is unavailable.

## Evidence semantics

- `FORBIDDEN_PROCESS_DETECTED / DETECTED`: the Service observed a concrete PID and
  process creation time.
- `ENFORCEMENT_ACTION / BLOCKED`: Windows no longer reports that same PID/creation
  identity after `taskkill` returned success.
- `ENFORCEMENT_ACTION / ALREADY_EXITED`: the process disappeared before termination
  could be confirmed as the result of EECP.
- `ENFORCEMENT_ACTION / QUERY_FAILED`: Windows did not establish whether the exact
  PID/creation-time identity still existed. It is not treated as exit or success;
  Service health degrades and the next maintenance cycle retries.
- `ENFORCEMENT_ACTION / FAILED`: the same process identity remained active.
- `ENFORCEMENT_TAMPER / CONFIG_CHANGED`: current OS state differs from the active
  policy. This is integrity evidence, not proof of intentional user tampering.
- `RECOVERED` is written only after verification; otherwise `RECOVERY_FAILED` is
  CRITICAL.
- `USER_SESSION_CHANGED` comes from SCM session-change notification, not network
  logon events or website authentication.

## Windows auditing

The implementation does not silently enable global Windows audit policy or install
SACLs. Therefore `ATTEMPT_DENIED` is not emitted by default. To attribute a denied
attempt, administrators must separately configure an approved Windows audit policy
and SACL for the specific protected object, then collect and correlate the relevant
Security log record. A state mismatch alone is reported as `CONFIG_CHANGED`.

## Limitations requiring Windows validation

- `Get-CimInstance Win32_Process`, owner lookup, PID/creation-time identity, and
  termination verification.
- SCM `SERVICE_CONTROL_SESSIONCHANGE` delivery and WTS username/domain lookup.
- ProgramData ACL application through `icacls`.
- Exact `Get-NetFirewallRule`/address-filter verification and recovery.
- Hosts and USBSTOR recovery under LocalSystem.

These paths require an isolated elevated Windows VM for acceptance. No result from a
mock or non-Windows test should be described as Windows E2E evidence.

Firewall rules are now created with explicit, identical PowerShell `-Name` and
`-DisplayName` values. Verification and deletion select the unique internal `Name`,
then verify `DisplayName`, Group, Enabled, Direction, Action, Profile, and
RemoteAddress before considering the rule healthy. EECP refuses deletion for names
outside its deterministic ownership format.

See `docs/v2/two-machine-demo-runbook.md` for installation, read-only preflight,
six acceptance scenarios, evidence collection, and cleanup.
