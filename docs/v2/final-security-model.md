# Final security model

## Trust rule and actors

The critical rule is: **local Authenticated User != Backend privileged command
authority**. Users (`ADMIN` and `EXAMINER`) authenticate to the Backend against
DB-backed accounts (`0003_users_and_auth.sql`) and receive signed JWTs with `jti`
revocation support and scoped RBAC permissions (`ADMIN` has full access and
`MANAGE_USERS`; `EXAMINER` is restricted to assigned `session_examiners` scope).
The Backend owns business truth and both signing capabilities. A Local Gateway and
an Agent Client authenticate as identity-bound machines and only route messages. An
interactive Student/local process is untrusted even though Windows may classify it
as an Authenticated User. The Agent Service runs under LocalSystem, verifies
Backend proofs, and alone mutates Windows. Windows Administrator or OS compromise
remains outside the anti-tamper guarantee.

## Controls

- `/health` is anonymous for orchestration. Operational `/api/v1/*` and
  `/api/v2/gateways*` HTTP paths require bearer authentication in
  `production-like` mode (or when `EECP_ENFORCE_AUTH=true`); routers enforce
  permission (`MANAGE_USERS`, `VIEW_SESSION`, `CONTROL_AGENT`, `VIEW_AGENT`,
  `VIEW_INCIDENT`) and `session_examiners` resource scope.
- `POST /api/v1/auth/logout` revokes the token `jti` in persistence. WebSocket
  `/api/v1/ws` re-hydrates the principal on `ping` and closes the socket with
  code `1008` if the token is revoked, expired, locked, or the account role changed.
- Gateway and Agent WSS identities use separate configured machine credentials,
  including revocation checks. Machine credentials are not Examiner tokens.
- Signed policy binds canonical policy content and session. Privileged command
  authorization separately binds protocol version, command ID, operation, session,
  target Agent, policy hash, issue/deadline, and correlation ID.
- APPLY and RESTORE are rejected before execution when unsigned, forged, tampered,
  expired, wrong-target, or wrong-session. HEALTH_CHECK is read-only and may remain
  unsigned.
- The local-only Named Pipe rejects remote clients and grants LocalSystem/Admin full
  access and Authenticated Users read/write. This ACL enables the normal-user Client;
  it does not authorize privileged work.
- A bounded atomic replay journal stores command fingerprint, expiry, and result.
  Same-ID/same-content returns the cached result; same-ID/different-content and old
  signed commands are rejected across Service reconstruction. It stores no key.
- Policy and command proofs use HMAC-SHA256. Verification keys are symmetric signing
  capability: Service-secret compromise can forge proofs. The Client and Gateway
  possess neither key. Asymmetric signing is future hardening.
- An Administrator can stop/replace the Service or read protected configuration.
  Static hosts/Firewall state may remain, but maintenance stops; absolute protection
  against Administrator-level compromise is not claimed.

## HTTP and WebSocket endpoint inventory

| Endpoint family | Actor | Permission/scope | Anonymous |
|---|---|---|---|
| `GET /health` | orchestrator | none | Yes |
| `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh` | `ADMIN` / `EXAMINER` | credential / refresh verification | Yes |
| `GET /api/v1/auth/me`, `POST /api/v1/auth/logout` | `ADMIN` / `EXAMINER` | authenticated bearer (`jti` revoked on logout) | No |
| `GET`/`POST /api/v1/users`, `PATCH /api/v1/users/{user_id}` | `ADMIN` | `MANAGE_USERS` | No |
| `PUT /api/v1/sessions/{session_id}/examiners` | `ADMIN` | `MANAGE_USERS` | No |
| `GET /api/v1/agents`, `GET /api/v2/gateways`, `GET /api/v2/gateways/agents/{agent_id}` | `ADMIN` / `EXAMINER` | `VIEW_AGENT`, scoped to assigned sessions for `EXAMINER` | No |
| `GET /api/v1/sessions`, `GET /api/v1/sessions/{session_id}` | `ADMIN` / `EXAMINER` | `VIEW_SESSION`, `session_examiners` scope unless `ADMIN` | No |
| `POST /api/v1/sessions`, `PATCH .../status`, `POST .../policy/deploy`, `POST .../start`, `POST .../finish` | `ADMIN` / `EXAMINER` | `CONTROL_AGENT` (`POST /api/v1/sessions` also restricted appropriately / scoped) | No |
| `GET /api/v1/sessions/{session_id}/security-events`, `GET /api/v1/sessions/{session_id}/summary` | `ADMIN` / `EXAMINER` | `VIEW_INCIDENT`, `session_examiners` scope | No |
| `GET/POST /api/v1/policy-profiles`, `PUT/DELETE /api/v1/policy-profiles/{profile_id}` | `ADMIN` / `EXAMINER` | `VIEW_SESSION` / `CONTROL_AGENT` (audited) | No |
| `GET /api/v1/sessions/{session_id}/screen/evidence`, `POST .../screen/requests`, `GET /api/v1/screen/...` | `ADMIN` / `EXAMINER` | `VIEW_SESSION` / `CONTROL_AGENT`, `session_examiners` scope | No |
| `WEBSOCKET /api/v1/ws` | `ADMIN` / `EXAMINER` | bearer/query token, scoped broadcast + `ping` re-hydration (`1008` on revoke/lock/role change) | No (when auth enforced) |
| `WEBSOCKET /api/v2/gateways/ws/{gateway_id}`, `POST /api/v2/screen/ingest/{gateway_id}` | Local Gateway | bound Gateway machine credential | No |
| `POST /api/v1/agents/register`, `POST /api/v1/agents/{agent_id}/heartbeat`, `GET /api/v1/agents/{target_id}/commands`, `POST /api/v1/commands/{command_id}/acknowledge` | Compatibility / Agent | blocked (`403`) in `production-like`; `MANAGE_USERS` / scoped checks when `auth_enforced` | No (when auth enforced) |

## Threat matrix

| Threat | Mitigation | Residual risk | Evidence/test |
|---|---|---|---|
| Student kills Client | Service owns persistent enforcement/maintenance | telemetry pauses; real lifecycle PENDING | Service lifecycle unit test |
| Student crafts RESTORE | Backend command HMAC, target/session/deadline checks | Service key compromise | forged RESTORE test |
| Student tampers policy | canonical hash + policy HMAC | symmetric key compromise | signed-policy tests |
| Student tampers signed Command | all security fields covered | symmetric key compromise | parametrized tamper tests |
| Student replays old command | expiry + persistent fingerprint journal | journal deletion by Administrator | restart replay test |
| Student direct-IP attempt | outbound Firewall IP/CIDR rules | real OS PENDING | Firewall unit tests |
| WAN failure | Gateway SQLite accepts and retries Events | Agent→Gateway pre-persistence window | WAN benchmark |
| Gateway restart | durable pending rows retained | local disk loss | reliability integration |
| Backend restart | Gateway reconnect/retry and Backend dedupe | volatile control messages retry upstream | reconnect tests |
| Redis loss | PostgreSQL remains truth; presence degrades/reannounces | temporary stale/unavailable presence | external Redis tests |
| Duplicate Event | unique event ID / idempotent persistence | retry traffic | dedupe tests |
| Lost EventReceipt | at-least-once retry, Backend dedupe | delayed cleanup | Phase 7 receipt tests |
| Revoked Agent credential | identity-bound validation and audit denial | config distribution latency | machine-auth tests |
| Revoked Gateway credential | identity-bound validation and audit denial | config distribution latency | machine-auth tests |
