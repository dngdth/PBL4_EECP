# Final security model

## Trust rule and actors

The critical rule is: **local Authenticated User != Backend privileged command
authority**. An Examiner authenticates to the Backend and receives scoped RBAC
permissions. The Backend owns business truth and both signing capabilities. A
Gateway and an Agent Client authenticate as identity-bound machines and only route
messages. An interactive Student/local process is untrusted even though Windows may
classify it as an Authenticated User. The Agent Service runs under LocalSystem,
verifies Backend proofs, and alone mutates Windows. Windows Administrator or OS
compromise remains outside the anti-tamper guarantee.

## Controls

- `/health` is anonymous for orchestration. Operational `/api/v1/*` and
  `/api/v2/gateways*` HTTP paths require Examiner bearer authentication in
  production-like mode; routers enforce permission and session/resource scope.
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

## HTTP endpoint inventory

| Endpoint family | Actor | Permission/scope | Anonymous |
|---|---|---|---|
| `/health` | orchestrator | none | Yes |
| `/api/v1/auth/login` | Examiner | credential verification | Yes |
| `/api/v1/agents`, `/api/v2/gateways*` reads | Examiner | `VIEW_AGENT` | No |
| Session reads | Examiner | `VIEW_SESSION`, assigned scope unless Admin | No |
| Session/policy control | Examiner | `CONTROL_AGENT`, session scope | No |
| Incident/summary reads | Examiner | `VIEW_INCIDENT`, session scope | No |
| Gateway Backend WSS | Gateway machine | bound Gateway credential | No |
| Gateway Agent WSS | Agent machine | bound Agent credential | No |
| Legacy direct-Agent HTTP | compatibility only | Examiner middleware/route checks | No in production-like |

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
