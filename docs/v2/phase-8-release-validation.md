# Phase 8.1 — Release validation

Validation date: 2026-09-28. Test certificates generated outside the repository were used only for isolated TLS integration. They are not production certificates.

| Item | Automated | Environment | Result | Evidence |
|---|---:|---|---|---|
| PostgreSQL | Yes | Isolated `postgres:17-alpine` on `127.0.0.1:55432` | PASS | Migration applied twice; Agent, Gateway, binding, Session, Policy, Command, event dedupe, Incident, audit persistence, restart, unique and FK constraints tested. |
| Redis | Yes | Isolated `redis:7.4-alpine` on `127.0.0.1:56379` | PASS | Set/read, TTL refresh, expiry, pub/sub, loss/flush, reannounce and unavailable health tested. |
| Docker API | Yes | Docker Desktop Linux engine | PASS | Image built; container `/health` returned database and Redis online. |
| Docker Gateway | Yes | Docker Desktop Linux engine | PASS | Image built and ran with TLS, writable SQLite buffer and production-like configuration. |
| Docker Web | Yes | Docker Desktop Linux engine | PASS | Multi-stage image built; Nginx served `/gateways` with HTTP 200 and SPA fallback. |
| Compose | Partial | Isolated project `eecpphase81` | PASS | PostgreSQL, Redis, migration, API and Web started. Host port 8000 was occupied, so isolated API validation used port 18000 without stopping the existing process. |
| TLS Backend | Yes | Ephemeral self-signed test certificate | PASS | Trusted HTTPS health request succeeded; untrusted certificate was rejected. |
| WSS Backend/Gateway | Yes | Ephemeral test CA/certificate and isolated containers | PASS | Gateway authenticated and reported backend uplink `ONLINE` over WSS. |
| WSS Agent/Gateway | Yes | Ephemeral test CA/certificate | PASS | Trusted WSS handshake succeeded; untrusted certificate was rejected. Authentication then closed the deliberately invalid credential with code 1008. |
| Windows Service | No | Current host is not elevated and is not an isolated VM | BLOCKED | Unsafe to install or exercise LocalSystem enforcement here. |
| Named Pipe real | No | No elevated isolated Windows VM | BLOCKED | CI loopback regression passes; real SCM/ACL path remains target-environment work. |
| Firewall real | No | No elevated isolated Windows VM | BLOCKED | Unit/integration adapters pass; no real firewall mutation was attempted. |
| Kill Client | No | No elevated isolated Windows VM | BLOCKED | Process-lifecycle/unit behavior remains covered; real enforcement persistence is unvalidated. |
| Restore | No | No elevated isolated Windows VM | BLOCKED | Fake-adapter restore is covered; real hosts/Firewall/USB restore remains unvalidated. |
| Security audit | Yes | SQLite regression plus PostgreSQL semantics | PASS | Authentication, authorization, revoked credentials and policy-signature denial use the existing persistent hash chain; tampering is detected and secrets are excluded. |
| Gateway UI | Yes | TypeScript build and Nginx runtime | PASS | List/detail show backend-reported identity, health, uplink, Agent count and buffer fields; unavailable fields remain unavailable. |

## Security audit scope

Examiner authentication failures, meaningful token failures, role/scope/session-state authorization denials, revoked or invalid Gateway authentication, Agent authentication reports through Gateway, and Service policy verification failures returned through ACK are persisted in the existing audit chain. The in-memory throttle suppresses repeated identical denial records for 30 seconds to limit anonymous amplification. Passwords, bearer tokens, and raw machine credentials are never included.

Agent and Gateway credentials are deployment configuration in Phase 8. There is no enrollment/rotation/revocation API, so no fictitious lifecycle operation was added. Attempts to use a configured revoked credential are audited.

## Gateway operational data

The flow remains Frontend → Backend → Gateway state. Gateway health messages now include durable-buffer count, oldest age, status, last successful flush and safe last error. The Backend combines persistent Gateway identity/health with Redis presence fields. The frontend never contacts a Gateway directly and does not invent timestamps or backlog thresholds.

Agent views expose the actual Agent/presence status, service health, active policy hash, Gateway binding, version, last seen and latest persisted incident. Missing operational fields are shown as unavailable rather than synthesized as success.

## Production configuration

Production-like Backend startup requires PostgreSQL, Redis, authentication material, per-Gateway credentials and the policy signing key. Known development secrets and placeholders are rejected. Production-like Gateway startup requires WSS, TLS listener files, per-Agent credentials and non-development bootstrap secrets; plaintext opt-in is rejected.

## HMAC security review

The Backend owns `EECP_POLICY_SIGNING_KEY`; the Agent Client never receives it. The privileged Service owns the verification material. Because Phase 8 uses HMAC-SHA256, the verification material is the same symmetric capability as the signing secret: compromise of the Service secret can produce a valid signature. Asymmetric signing remains future hardening and was not introduced in Phase 8.1.

## Remaining target-environment validation

An elevated, isolated Windows VM must still validate SCM installation/recovery under LocalSystem, real Named Pipe ACLs, hosts/Firewall/process/USB enforcement, kill-Client persistence, maintenance, restore, and preservation of unrelated firewall rules. These are environment blockers, not claimed as PASS.
