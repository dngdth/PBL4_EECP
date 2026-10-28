# Phase 8 — Data, Security, Frontend & Release

> **Validation status superseded.** The implementation description remains
> historical Phase 8 design evidence. Its original external-service blockers were
> resolved and validated in `docs/v2/phase-8-release-validation.md` (Phase 8.1).
> Use that document, not the closing Phase 8 snapshot below, for PostgreSQL,
> Redis, Docker, TLS, and WSS validation status.

## 1. Goals

Move production-like business persistence to PostgreSQL, use Redis only for
ephemeral presence, harden human and machine authentication, verify signed
policies in the privileged Service, expose real operational state, and provide
repeatable release and rollback procedures without changing Protocol v2 routes.

## 2. Architecture

```text
Dashboard -> Backend -> PostgreSQL (business truth)
                     -> Redis (ephemeral presence)
Backend -> Gateway -> Agent Client -> Named Pipe -> Agent Service
Sensor -> Gateway SQLite buffer -> Backend dedupe
```

### Storage inventory

| Entity/data | Current storage | Lifetime | Phase 8 target |
|---|---|---|---|
| Agent registry | Backend SQLite | persistent | PostgreSQL |
| Gateway registry/binding | Backend SQLite | persistent | PostgreSQL |
| Session/workstation assignment | Backend SQLite | persistent | PostgreSQL |
| Policy profile/snapshot | Backend SQLite/session payload | persistent | PostgreSQL |
| Command/ACK | Backend SQLite | persistent | PostgreSQL |
| Event/dedupe | Backend SQLite | persistent | PostgreSQL, unique `event_id` |
| Incident | Backend SQLite | persistent | PostgreSQL |
| Audit hash chain | Backend SQLite | persistent | PostgreSQL |
| Agent/Gateway presence | process memory/current registry rows | ephemeral | Redis TTL |
| Gateway event buffer | Gateway-local SQLite | durable local | keep SQLite |
| Service enforcement baseline | Agent Service local state | machine-local | unchanged |

## 3. PostgreSQL

The domain and application continue to depend on repository/UoW ports. The
PostgreSQL adapter is synchronous to match the current Backend and uses bounded
connection pooling. Versioned SQL migrations own production schema creation;
SQLite remains an explicit test/lightweight-development adapter.

## 4. Redis

Redis stores only current Agent/Gateway presence with configurable TTL. Redis
loss degrades realtime state but cannot destroy sessions, policy, commands,
events, incidents, or audit history. Reconnect and heartbeat rebuild state.

## 5. Authentication

Examiner, Agent, and Gateway identities use distinct authentication paths.
Machine credentials are bound to one identity and support revocation. Secrets
come from environment or protected local configuration and are never returned
by APIs.

## 6. Authorization

Server-side authorization combines role, permission, session/resource scope,
and session state. Frontend checks are presentation only and never authoritative.

## 7. Policy signing

Production-like policy signing uses HMAC-SHA256. Backend signer and Agent Service
verifier receive the shared key through protected deployment configuration; the
normal-user Client never receives it. The signature covers canonical policy
content plus session identity. Unsigned,
expired, hash-mismatched, or tampered policies are rejected before enforcement.
No private key is stored in this repository.

## 8. Audit

Sensitive authentication, authorization, policy, session, credential, and
control operations append to the existing tamper-evident audit chain without
recording secrets.

## 9. Frontend operational states

The Dashboard renders Backend facts for Gateway health, Agent/Service presence,
policy hash, command progress, incidents, and Gateway buffer backlog. It does not
synthesize successful preflight or Firewall state.

## 10. Deployment

Production-like compose starts PostgreSQL and Redis with health checks, runs
migrations explicitly, then starts API, Gateway, and the static Vite web image.
Agent Client and Agent Service remain Windows-host components.

## 11. Windows Service installation

See the release runbook for install, recovery policy, start/stop, verification,
and safe uninstall/restore commands.

## 12. Health checks

Backend health reports PostgreSQL and Redis independently. Gateway health exposes
uplink and durable-buffer state. Service health continues over Named Pipe.

## 13. Logging/correlation

Structured logs preserve command, correlation, event, session, Agent, and
Gateway identifiers. Credentials, signing material, and full sensitive payloads
are excluded.

## 14. Migration

Apply versioned migrations before Backend startup. Development SQLite data may be
discarded or imported with the documented one-shot procedure; production data is
never silently reset or auto-migrated destructively.

## 15. Rollback

Application rollback is allowed only across schema-compatible releases. Database
downgrade limitations, credential revocation, Gateway buffer preservation, and
Agent baseline restoration are covered by the release runbook.

## 16. E2E release checklist

The repeatable checklist covers infrastructure readiness, authentication, signed
policy application, hash ACK, detection, WAN buffering/recovery, dedupe, restore,
and audit verification, repeated three times where the environment supports it.

## 17. Security limitations

Production TLS certificates, executable signing, and elevated isolated-Windows
validation depend on deployment-owned infrastructure and must never be reported
as complete when unavailable.

## 18. Future optional scaling

Multiple Backend instances, load balancing, Gateway HA, a broker, custom WFP,
auto-update, and richer analytics remain conditional on benchmark/threat-model
evidence and are not Phase 8 work.

### Validation result

Local CI-safe validation: 246 tests passed with 9 existing deprecation warnings;
Ruff, frontend lint/build, compose rendering, and three repeated vertical release
scenarios passed. Real PostgreSQL and Redis integration, container image builds,
TLS, and elevated Windows validation are blocked because Docker Desktop is not
running and this workstation is neither elevated nor an isolated release VM.
