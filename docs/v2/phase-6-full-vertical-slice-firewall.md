# Phase 6 — Full Vertical Slice + Windows Firewall

## 1. Goal

Phase 6 first proves the complete Protocol v2 command/result path with a
`HEALTH_CHECK`. Only after that test passes does it add service-owned outbound
Windows Firewall enforcement for explicit IP addresses and CIDR networks.

## 2. Architecture

The authority and privilege boundaries remain unchanged:

```text
Central Backend -> Local Gateway -> Agent Client -> Named Pipe -> Agent Service
                                                         |
                                                         v
                                      hosts / Firewall / process / USB
```

The Backend creates and persists commands. The Gateway is a transparent router.
The normal-user Client translates a routed command into a named-pipe request.
Only the privileged Service owns operating-system enforcement and maintenance.

## 3. Full vertical command flow

The Phase 6 integration harness exercises this path without replacing a hop:

```text
Backend Command
  -> Backend Gateway uplink router
  -> Local Gateway target router
  -> Agent Client
  -> NamedPipePrivilegedExecutor (framed Protocol v2 bytes)
  -> ServiceProtocolHandler / ExecutionService
  -> ServiceResult
  -> Agent Client ACK
  -> Local Gateway
  -> Backend ACK handler and repository
```

`command_id`, `session_id`, `target_id`, `policy_hash`, and `correlation_id` are
preserved wherever the applicable Protocol v2 contract carries them. A command
is never retargeted by the Gateway.

## 4. HEALTH_CHECK validation

The first Phase 6 test uses the complete route above, verifies that only the
target Agent receives the command, and checks Backend command persistence after
the returned ACK. `HEALTH_CHECK` performs no OS mutation. Firewall work is gated
on this test passing.

## 5. Network enforcement model

Design target for the second half of Phase 6:

```text
Domain rule -> managed hosts-file block
IP rule     -> managed Windows Firewall outbound block
CIDR rule   -> managed Windows Firewall outbound block
```

The policy contract remains Protocol v2. New IP/CIDR fields must be optional so
existing policy documents and their canonical hashes remain stable.

## 6. Firewall rule identity

Rule names use this deterministic format:

```text
EECP-{SHA256(session_id)[0:12]}-{IP|CIDR}-{SHA256(canonical_address)[0:12]}
```

For example, the logical input `SES-001` plus `203.0.113.10` always produces the
same name. The group is `EECP-{session digest}`. The persisted state stores exact
managed names plus canonical IP/CIDR inputs. Runtime names, Gateway addresses,
hostnames, and timestamps never enter the policy hash.

## 7. Management path protection

The Service resolves `EECP_GATEWAY_URL` from trusted local configuration at
startup and passes only the resulting IPv4/IPv6 addresses to the Firewall
adapter. Commands cannot provide or expand this allowlist. An exact blocked IP
or a CIDR containing a protected Gateway address rejects the whole apply with
`INVALID_POLICY` before hosts or Firewall mutation. Backend access is not added
because the production Agent control path reaches Backend only through Gateway.

## 8. Apply semantics

Validation and management-overlap checks run first. Hosts are updated, desired
Firewall state is reconciled and verified, USB transition and process controls
then run, and only after all steps succeed is the state (including active hash)
atomically replaced. The applied hash therefore means “fully enforced”, not
merely “received”. Rules are outbound `Block` rules with `RemoteAddress` set to
the canonical IP/CIDR. `profile=any` intentionally covers Domain, Private, and
Public profiles so a network-category transition cannot bypass an exam rule;
the narrow destination scope and protected-Gateway check limit that choice.

## 9. Verification

`WindowsFirewallEnforcer` queries every exact managed rule and verifies its name,
direction, action, and remote address after reconciliation. Service-owned
`maintain()` repeats reconciliation from persisted desired state and recreates a
missing rule. No packet probe is performed during routine apply.

## 10. Rollback

Before mutation the enforcer retains the current hosts content, USB value when a
transition is required, previous managed Firewall plan, and previous state file.
On a failure it restores the exact hosts content, reconciles Firewall back to the
previous EECP-owned plan, restores USB if changed, keeps the previous persisted
state, and returns `EXECUTION_FAILED`. This is best-effort OS rollback: a machine
failure or administrator interference during rollback cannot be made atomic.

## 11. Restore

Restore removes the managed hosts block, restores the saved USB baseline, and
deletes only exact names recorded as EECP-owned. The adapter refuses to remove a
name that does not match the deterministic EECP identity. It never resets or
disables Windows Firewall and never enumerates/deletes unrelated rules.

## 12. Idempotency

Repeated apply converges to one logical rule per policy item. An already-correct
rule is retained; an incorrect rule with the same owned name is replaced. Policy
updates remove stale owned names before verification. Repeated restore succeeds
when the state/rules are already absent.

## 13. Audit-only mode

Audit mode validates and records the intended domains, IPs, CIDRs, applications,
and USB state. It never constructs the real Firewall adapter or mutates the OS.

## 14. Security constraints

- No shell invocation or policy-provided command text.
- No WFP, kernel, or NDIS driver.
- Structured addresses are validated with Python's `ipaddress` module.
- Restore cannot reset or disable Windows Firewall.
- `netsh` receives a fixed argument list with only canonical `ipaddress` output.
- IPv4 and IPv6 addresses/networks are supported; CIDRs must be canonical network
  addresses (`strict=True`).

## 15. Tests

Automated coverage includes full HEALTH_CHECK/APPLY/RESTORE round trips, direct
IP and CIDR planning, IPv4/IPv6, malformed input and injection strings,
management overlap, deterministic identities, duplicate apply/restore, policy
change reconciliation, non-EECP preservation, maintenance recreation, rollback,
audit mode, architecture boundaries, Named Pipe protocol, and Gateway routing.

Final local result: `223 passed, 9 warnings`.

## 16. Windows VM validation

Real rule creation is run only on an explicitly isolated, elevated Windows VM
and must always clean up `EECP-TEST-` rules in `finally`. The current workstation
was not established as an isolated test VM, so real rule creation/query/removal
is reported as **BLOCKED**, not simulated as a pass.

## 17. Known limitations

- Firewall verification parses `netsh` text and therefore depends on recognizable
  `Out`/`Block` output tokens on the installed Windows locale.
- Gateway DNS is resolved at Service startup; address changes require a Service
  restart before the protected set changes.
- Persisted enforcement survives a Client exit and maintenance is Service-owned,
  but after a Service restart `ExecutionService` does not reconstruct its in-memory
  active hash for HEALTH_CHECK from the policy state file.
- Policy signatures remain optional and are not cryptographically verified.
- Gateway messages remain volatile during WAN/uplink loss; durable buffering is
  reserved for Phase 7.
- Killing the privileged Service stops maintenance. Static hosts/Firewall state
  can remain, but Phase 6 does not claim anti-tamper protection against an
  administrator.

## 18. Phase 7 readiness

Phase 7 may add Sensor → Violation Event → Gateway → Backend, reconnect/retry,
a Gateway SQLite durable event buffer, event deduplication, and presence
consistency while retaining the Backend → Gateway → Agent → Service command path
and this Firewall boundary. None of those features are implemented here.
