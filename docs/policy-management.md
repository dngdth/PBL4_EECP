# Phase 3 Policy Management

## Flow

```text
Teacher selects Exam Profile
  -> POST /api/v1/sessions
  -> Control Server creates session + policy + commands atomically
  -> Agent polls GET /api/v1/agents/{agent_id}/commands
  -> Agent applies Windows controls
  -> Agent POSTs /api/v1/commands/{command_id}/acknowledge
  -> Dashboard reports the per-Agent policy status
```

The transport is short polling on the existing five-second Agent loop. Commands
are durable in SQLite across Control Server restarts and temporary disconnects,
subject to the Phase 3.1 delivery deadline below.

## Phase 3.1 delivery guarantees

The server marks a command `DELIVERED` when it is returned to an Agent. Until an
ACK arrives, the same command becomes available again after 10 seconds. It is
delivered at most three times and has an absolute one-minute lifetime. On the
next Agent poll after either limit, the command becomes `TIMED_OUT`, its policy
status becomes `FAILED`, and a `COMMAND_TIMED_OUT` audit event is recorded.
Delivery metadata is persisted in SQLite (`attempt_count`, `last_attempt_at`,
`next_retry_at`, and `expires_at`), including for upgraded databases.

A direct-management session cannot move from `CREATED` to `READY` until every
assigned Agent is online and has acknowledged the exact policy hash, producing
the `APPLIED` status. The server also rejects assigning an Agent that is already
reserved by another non-terminal session with HTTP `409` and code
`SESSION_CONFLICT`. Finished sessions release their assignments; a workstation
whose policy delivery failed is also released so the teacher can recover by
creating a replacement session.

## Phase 3.2 dynamic profile management

Policy profiles are persisted in SQLite and managed at `/policies`. The two
built-in recovery profiles are seeded idempotently whenever the database is
initialized. Teachers can add reusable custom profiles, edit their rules, view
the generated YAML, and delete profiles that have never been used by a session.

The API contract is:

- `GET /api/v1/policy-profiles` lists built-in and custom profiles.
- `POST /api/v1/policy-profiles` creates a custom profile.
- `PUT /api/v1/policy-profiles/{profile_id}` updates a custom profile.
- `DELETE /api/v1/policy-profiles/{profile_id}` deletes an unused custom profile.

Profile IDs are normalized to uppercase and may contain letters, digits, and
underscores. The domain validates the rule sections, rejects duplicate values
and application allow/deny overlap, and normalizes executable/category names to
lowercase. Built-in profiles are read-only. Deleting a profile referenced by a
session returns `409 POLICY_IN_USE`, preserving the historical policy catalog.
Updating a custom profile affects only sessions created afterward because every
session stores an immutable policy snapshot and hash at creation time.

## Policy profile contract

`GET /api/v1/policy-profiles` returns the selectable catalog, rule object, and a
generated YAML preview. Session creation accepts `policy_profile`:

```json
{
  "name": "PBL4 Final",
  "room": "A101",
  "agent_ids": ["PC01", "PC02"],
  "policy_profile": "INTERNET_NO_AI"
}
```

For backward compatibility, omitting `policy_profile` selects
`INTERNET_NO_AI`. Its generated YAML is equivalent to:

```yaml
policy:
  profile: INTERNET_NO_AI
  rules:
    applications:
      allow:
        - vscode.exe
        - gcc.exe
      deny:
        - chatgpt.exe
        - anydesk.exe
        - teamviewer.exe
    network:
      block:
        - generative_ai
        - social_network
    devices:
      usb: deny
```

Each policy contains a monotonically increasing version and a SHA-256 hash over
its normalized profile, rules, and version. The Agent must ACK that exact hash;
an unexpected hash is rejected and the command remains pending.

## Lifecycle and recovery

Policy assignment does not replace the Phase 2 management lifecycle. A new
session remains `CREATED` while its per-workstation policy status progresses
from `PENDING` to `APPLIED` or `FAILED`. The teacher can observe that result on
the session dashboard. The `Mark Ready` action appears only after every Agent
reports `APPLIED`.

At `FINISHED`, the server creates `RESTORE_BASELINE` commands for all assigned
Agents. Restore is idempotent. The Agent keeps a local state snapshot so the
previous USB setting can be restored even after an Agent restart. The hosts
file update uses explicit begin/end markers and never removes entries outside
that block.

The pre-existing gateway pipeline continues to use its original
`DEPLOYING -> PREFLIGHT` policy acknowledgement flow. Direct management ACKs do
not move session state, preventing the two lifecycle modes from being mixed.

## Clean Architecture placement

- The domain profile model validates selectable business rules, not YAML formatting.
- Application policy use cases manage profile CRUD, fetch commands, and process ACKs.
- The presentation layer generates the YAML preview and exposes HTTP schemas/routes.
- SQLite profile/command repositories remain infrastructure adapters behind Unit of Work ports.
- The Agent application layer depends on policy-enforcement and command-client
  protocols; Windows and HTTP implementations live under `agent/infrastructure`.
