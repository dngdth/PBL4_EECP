# EECP Protocol v2

Protocol v2 is the shared, transport-independent message contract for future
Backend, Local Gateway, Agent Client, and privileged Agent Service adapters. The
contract source of truth is `contracts/v2/`; checked-in JSON examples live in
`contracts/v2/examples/` and are validated by the test suite.

## 1. Goals

- Define one versioned JSON vocabulary for policy, command, execution ACK, event,
  presence, and the future privileged-service boundary.
- Give commands, acknowledgements, events, policies, and service requests stable,
  opaque identities and end-to-end correlation.
- Require timezone-aware UTC timestamps, explicit enum values, strict known fields,
  and rejection of unsupported protocol major versions.
- Protect command idempotency and policy-hash stability without changing the current
  HTTP runtime.
- Restrict executable operations and their payload shapes at schema level.

## 2. Non-goals

Phase 1 does not implement a Local Gateway, WSS/WebSocket transport, Named Pipe,
Windows Service, process split, ACL, Windows Firewall, PostgreSQL, Redis, RBAC, or
new enforcement/sensor behavior. It does not migrate existing HTTP endpoints or
change the current Agent polling loop. `ServiceRequest` and `ServiceResult` are data
contracts only; there is no IPC transport in this phase.

## 3. Message lifecycle

```text
PolicyEnvelope
      |
      v
Command(command_id, correlation_id)
      |
      +--> future ServiceRequest(request_id, same command/correlation)
      |          |
      |          v
      |     ServiceResult(execution result)
      |
      v
Ack(ack_id, same command/correlation, execution result)
```

An ACK represents the result of executing an allowlisted command. It is not a
delivery receipt. Delivery state (`PENDING`, `DELIVERED`, retries, and timeout) stays
in the current backend domain and is distinct from Protocol v2 execution-result ACK.

## 4. Common conventions

| Convention | Rule |
| --- | --- |
| Protocol version | Required integer literal `2`; unknown major versions are rejected. |
| IDs | Required non-empty opaque strings. Consumers must not infer business meaning from prefixes or formatting. |
| Message identity | `command_id`, `ack_id`, `event_id`, and `request_id` identify their respective messages/results. |
| Correlation | A command, service request/result, and ACK preserve the same `correlation_id`. |
| JSON | UTF-8 JSON only. Pickle and Python-specific binary formats are not contracts. |
| Unknown fields | Forbidden at every model level. Producers must not add fields until consumers support the revised schema. |
| Mutability | Pydantic models are frozen after validation. |
| Nulls | Optional fields may be `null` on input; `to_json()` omits fields whose value is `None`. |

The models use Pydantic but do not import FastAPI, a transport, persistence, backend
domain code, or Agent infrastructure.

## 5. PolicyEnvelope

| Field | Type | Required | Meaning | Validation |
| --- | --- | --- | --- | --- |
| `protocol_version` | integer | yes | Protocol major | Must equal `2` |
| `policy_id` | opaque string | yes | Stable policy/profile identity | Non-empty |
| `policy_version` | integer | yes | Monotonic policy content version | At least `1` |
| `policy_hash` | string | yes | Canonical SHA-256 digest | Exactly 64 lowercase hex characters and must match content |
| `session_id` | opaque string | yes | Session receiving this issued policy | Non-empty |
| `issued_at` | timestamp | yes | Issue time | UTC and timezone-aware |
| `expires_at` | timestamp/null | no | End of policy validity | UTC; strictly later than `issued_at` |
| `rules` | `PolicyRules` | yes | Immutable validated rule snapshot | Only applications/network/devices sections and known nested fields |
| `signature` | string/null | no | Future authenticity proof | Optional during compatibility period; not verified in Phase 1 |

`PolicyRules` supports the implemented rule vocabulary:

- `applications.allow` and `applications.deny`: unique, non-empty strings with no overlap;
- canonical v2 `network.block`: unique values from `generative_ai`, `social_network`, and `vpn_proxy`;
- canonical v2 `devices.usb`: `allow` or `deny`.

For hash-preserving migration of current legacy pipeline policies, the schema also
accepts `network.blocked_categories`, `network.allow_domains`, and
`devices.usb_storage`. A message cannot provide both the canonical and legacy name
for the same rule. New v2 producers use `block` and `usb`; the legacy names exist only
for the compatibility window because renaming them would change current hashes.

The envelope is frozen, and validation recomputes the canonical hash. Changing rules
while retaining the old hash therefore fails validation. Signature verification and
key distribution are intentionally deferred; Phase 1 does not create PKI.

## 6. Command

| Field | Type | Required | Meaning | Validation |
| --- | --- | --- | --- | --- |
| `protocol_version` | integer | yes | Protocol major | Must equal `2` |
| `command_id` | opaque string | yes | Idempotency identity | Non-empty |
| `command_type` | enum | yes | Allowlisted operation | `APPLY_POLICY`, `RESTORE_BASELINE`, or `HEALTH_CHECK` |
| `target_id` | opaque string | yes | Intended Agent/service target | Non-empty |
| `session_id` | opaque string | yes | Owning session | Non-empty |
| `issued_at` | timestamp | yes | Creation time | UTC and timezone-aware |
| `deadline` | timestamp | yes | Last acceptable execution time | UTC and not earlier than `issued_at` |
| `policy_hash` | SHA-256/null | conditional | Policy being applied/restored | Required and matched for apply; prohibited for health check |
| `payload` | typed object | yes | Operation-specific data | Must match `command_type` |
| `correlation_id` | opaque string | yes | End-to-end trace identity | Non-empty |

Payloads are not generic execution instructions:

- `ApplyPolicyPayload` contains a complete validated `PolicyEnvelope`.
- `RestoreBaselinePayload` accepts only `{"baseline": "NORMAL"}`.
- `HealthCheckPayload` contains only an optional opaque nonce.

There is no `RUN_SHELL`, `CMD`, `POWERSHELL`, `EXEC`, `RUN_PROGRAM`, arbitrary file,
arbitrary registry, or script operation.

## 7. Ack

| Field | Type | Required | Meaning | Validation |
| --- | --- | --- | --- | --- |
| `protocol_version` | integer | yes | Protocol major | Must equal `2` |
| `ack_id` | opaque string | yes | Stable result-message identity | Non-empty |
| `command_id` | opaque string | yes | Command whose execution completed | Non-empty |
| `status` | enum | yes | Execution result | `SUCCEEDED`, `FAILED`, or `REJECTED` |
| `applied_hash` | SHA-256/null | no | Hash actually active after execution | Lowercase SHA-256 when present |
| `service_version` | opaque string | yes | Executor version | Non-empty |
| `error_code` | enum/null | conditional | Machine-readable failure reason | Required for failed/rejected; absent for success |
| `error_message` | string/null | no | Human diagnostic | Must be absent for success |
| `occurred_at` | timestamp | yes | Completion time | UTC and timezone-aware |
| `correlation_id` | opaque string | yes | Trace identity from command | Non-empty |

The current HTTP ACK body (`success`, `policy_hash`, `error`, `actor`) remains active.
A future adapter maps it to/from this richer execution-result vocabulary; Phase 1 does
not change the endpoint.

## 8. Event

| Field | Type | Required | Meaning | Validation |
| --- | --- | --- | --- | --- |
| `protocol_version` | integer | yes | Protocol major | Must equal `2` |
| `event_id` | opaque string | yes | Dedupe identity | Non-empty |
| `session_id` | opaque string | yes | Owning session | Non-empty |
| `agent_id` | opaque string | yes | Producing Agent | Non-empty |
| `event_type` | enum | yes | Event vocabulary | Known value required |
| `occurred_at` | timestamp | yes | Observation time | UTC and timezone-aware |
| `sequence` | non-negative integer/null | no | Producer-local ordering hint | At least zero |
| `payload` | JSON object | yes | Non-executable event details | JSON-compatible values only |
| `correlation_id` | opaque string/null | no | Related flow identity | Non-empty when present |

The initial vocabulary is `POLICY_VIOLATION`, `FORBIDDEN_PROCESS_DETECTED`,
`SERVICE_HEALTH`, `DEVICE_EVENT`, `POLICY_INTEGRITY`, and `NETWORK_ATTEMPT`. These are
contract names only; Phase 1 adds no new sensor. Adding an event type requires a
reviewed contract revision and tests rather than accepting arbitrary strings.

## 9. Presence

| Field | Type | Required | Meaning | Validation |
| --- | --- | --- | --- | --- |
| `protocol_version` | integer | yes | Protocol major | Must equal `2` |
| `agent_id` | opaque string | yes | Agent identity | Non-empty |
| `gateway_id` | opaque string/null | no | Future reporting Gateway | Non-empty when present |
| `last_seen` | timestamp | yes | Last heartbeat observation | UTC and timezone-aware |
| `health` | enum | yes | Derived presence | `ONLINE`, `DEGRADED`, or `OFFLINE` |
| `active_policy_hash` | SHA-256/null | no | Reported active policy | Lowercase SHA-256 when present |
| `service_health` | enum/null | no | Future privileged service state | `HEALTHY`, `DEGRADED`, or `UNAVAILABLE` |
| `agent_version` | opaque string/null | no | Agent build/version | Non-empty when present |

A heartbeat is a signal. Presence is ephemeral state derived from signals and TTL;
an Agent does not need to announce `OFFLINE`. Presence is not an incident and is not
evidence of misconduct.

## 10. ServiceRequest/ServiceResult

Phase 1 defines these data contracts so Phase 2 can place a
`PrivilegedExecutionPort` between command processing and enforcement without
inventing another vocabulary. No Named Pipe or Windows Service exists yet.

`ServiceRequest` contains `request_id`, `command_id`, allowlisted `operation`,
`session_id`, conditional `policy_hash`, its typed payload, and `correlation_id`.
It enforces the same operation/payload/hash/session rules as `Command`.

`ServiceResult` contains `request_id`, `command_id`, execution `status`, optional
`applied_hash`, `service_version`, structured error details, `occurred_at`, and
`correlation_id`. Its success/failure invariants match `Ack`.

## 11. Error codes

| Code | Meaning |
| --- | --- |
| `UNSUPPORTED_PROTOCOL_VERSION` | The protocol major version is unsupported. |
| `INVALID_MESSAGE` | The message is malformed or fails contract validation. |
| `INVALID_COMMAND` | Command fields or payload are invalid. |
| `UNSUPPORTED_OPERATION` | The operation is not allowlisted. |
| `POLICY_HASH_MISMATCH` | Policy content differs from its declared hash. |
| `POLICY_EXPIRED` | Policy validity has ended. |
| `SESSION_MISMATCH` | Message belongs to another session. |
| `TARGET_MISMATCH` | Message is addressed to another consumer. |
| `INVALID_POLICY` | Policy rules or values are invalid. |
| `COMMAND_EXPIRED` | Command deadline has passed. |
| `DUPLICATE_COMMAND` | Command identity has already been processed. |
| `EXECUTION_FAILED` | Allowlisted execution failed. |
| `INTERNAL_ERROR` | Unexpected internal processing failure. |

The enum values are stable machine codes. Human messages are diagnostic and must not
be parsed for control flow.

## 12. Versioning rules

- The major integer is `2`; consumers reject any other value and never interpret
  `latest`.
- Breaking field, validation, or semantic changes require a new major version.
- Within major v2, a new optional field is permitted only after affected consumers
  have shipped support. Because unknown fields are forbidden, rollout must update
  consumers before producers emit the field.
- Deprecation requires documentation, a compatibility window, and tests. A required
  field is not removed or repurposed within v2.
- Legacy payloads have no implicit v2 default. They remain on existing adapters until
  explicitly mapped.

## 13. Idempotency semantics

- `command_id` is the idempotency key for command execution. A consumer persists or
  caches the first terminal result and returns that result for a duplicate instead of
  executing again.
- `event_id` is the dedupe key for event ingestion.
- `ack_id` identifies an ACK message; `command_id` links multiple delivery attempts
  to one execution result.
- `request_id` deduplicates a service-boundary request, while `command_id` preserves
  the higher-level operation identity.
- `correlation_id` is for tracing, not deduplication.

These are contract semantics only in Phase 1; no new persistence or retry behavior is
implemented.

## 14. Timestamp rules

All timestamps must be timezone-aware UTC. JSON examples use ISO-8601 with `Z`, for
example `2026-09-28T10:20:31Z`. Naive datetimes and non-UTC offsets are rejected.
Consumers compare deadlines using a UTC clock. Schema validation ensures ordering
(`deadline >= issued_at`, `expires_at > issued_at`) but does not reject an otherwise
well-formed historical message based on wall-clock time; the consumer reports
`COMMAND_EXPIRED` or `POLICY_EXPIRED` at processing time.

## 15. Policy hash semantics

### Current hash semantics

The current backend `PolicyDocument.create` hashes exactly:

```json
{"profile":"<UPPERCASE PROFILE>","rules":{...},"version":1}
```

It uses Python `json.dumps` with `ensure_ascii=False`, `sort_keys=True`, and compact
separators `(',', ':')`, encodes the result as UTF-8, then computes SHA-256 and emits
a 64-character lowercase hexadecimal digest. Object field order does not affect the
hash; array order does. `session_id`, issue/expiry timestamps, and signature are not
part of the current digest.

### Protocol v2 hash semantics

Protocol v2 intentionally preserves that algorithm for compatibility:

- `policy_id` maps to current `profile` and is uppercased only in the canonical hash
  payload;
- `policy_version` maps to current `version`;
- validated rules serialize with the current field names and values. Canonical v2
  uses `block`/`usb`, while existing `blocked_categories`/`allow_domains`/
  `usb_storage` are retained when mapping legacy pipeline policies so their digest
  remains unchanged.

`PolicyEnvelope` recomputes and checks the digest during parsing. Tests compare the
shared implementation with the existing backend `PolicyDocument`, cover key-order
and Unicode stability, and pin checked-in example hashes. A future hash migration
must introduce explicit semantics/versioning; it must not silently change this digest.

## 16. Legacy compatibility

| Current field | Protocol v2 field | Action | Notes |
| --- | --- | --- | --- |
| `PolicyDocument.profile` | `policy_id` | rename/map | Current profiles are normalized uppercase. |
| `PolicyDocument.version` | `policy_version` | rename/map | Positive integer retained. |
| `PolicyDocument.policy_hash` | `policy_hash` | reuse | Canonical algorithm unchanged. |
| `PolicyDocument.rules` | `rules` | validate/reuse | Current normalized applications/network/devices shape. |
| no issued envelope metadata | `session_id`, `issued_at`, `expires_at`, `signature` | new | Signature nullable during compatibility window. |
| `Command.id` | `command_id` | rename/map | Stable execution/idempotency identity. |
| `Command.type` | `command_type` | rename/map | Current two values retained; `HEALTH_CHECK` is v2-only. |
| `Command.created_at` | `issued_at` | rename/map | Existing timestamps are UTC-aware. |
| `Command.expires_at` | `deadline` | rename/map | Current one-minute TTL can map directly. |
| `Command.target_id`, `session_id` | same | reuse | Opaque target/session identities. |
| untyped command `payload` | typed `payload` | new adapter required | Current runtime payload is unchanged in Phase 1. |
| ACK `success` | ACK `status` | map | `true -> SUCCEEDED`; failures map to `FAILED` or `REJECTED` by adapter policy. |
| ACK `policy_hash` | `applied_hash` | rename/map | Execution result, not delivery receipt. |
| ACK `error` | `error_code` + `error_message` | split/map | Legacy free text remains on current endpoint. |
| ACK `actor` | `service_version` plus target context | replace in future adapter | No endpoint change now. |
| `TelemetryEvent.id` | `event_id` | rename/map | Dedupe identity. |
| `TelemetryEvent.workstation_id` | `agent_id` | rename/map | Current workstation Agent identity. |
| `Incident` | backend projection of events | no wire mapping | Remains current backend domain state, not a Phase 1 message. |
| `AuditEvent` | backend audit projection | no wire mapping | Hash-chained persistence remains unchanged; it is not an Agent command/event contract. |
| `Agent.id` | `agent_id` | rename/map | Stable opaque identity. |
| `Agent.status` | `health` | map | Current ONLINE/OFFLINE subset; backend derives OFFLINE by TTL. |
| `Agent.last_seen`, `agent_version` | same | reuse | Presence remains ephemeral. |
| no common correlation | `correlation_id` | new | Carries command-to-service-to-ACK tracing. |

`PolicyEnvelope.from_legacy` is the only compatibility helper added. No current
router, use case, repository, Agent client, or command processor imports v2 contracts,
so Phase 0 behavior and wire payloads remain unchanged.

## 17. Security constraints

- Protocol v2 **MUST NOT provide arbitrary remote execution**.
- Only the three enumerated operations and their typed payloads are valid.
- Unknown fields and unknown enum values are rejected.
- IDs are untrusted opaque input; authorization must not be inferred from their shape.
- A valid schema or hash is not authorization, authenticity, or proof of origin.
- `signature=null` is compatibility-only. Authentication, key management, signature
  verification, replay storage, authorization/RBAC, and secure transport are future
  responsibilities.
- Error messages must not include secrets or sensitive local system details.
