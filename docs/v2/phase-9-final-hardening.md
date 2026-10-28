# Phase 9 — Final Security Hardening, Benchmark, and Documentation Freeze

## Phase 9 review note

Baseline reviewed at commit `4496f16cb00e1d730408b8c50952c721cbe64014` on
`feat/khoa`. The clean-tree regression collected 266 tests: 264 passed, two
external-service tests were skipped, and nine deprecation warnings were reported.

### Security findings

- The production-like HTTP middleware protects `/api/v1` and only the exact
  `/api/v2/gateways` path. Consequently the operational mapping endpoint below
  that prefix is not authenticated. Authentication coverage must be prefix-aware,
  while `/health` remains intentionally anonymous for orchestrator probes.
- Endpoint authentication and endpoint authorization are separate. Several
  Examiner reads currently rely only on middleware authentication and do not state
  their permission or session scope at the router boundary.
- The Named Pipe is local-only and excludes World and Anonymous, but its
  Authenticated Users ACE does not establish Backend command authority. A local
  student process can construct a valid Protocol v2 restore request unless
  state-changing commands carry an independently verifiable Backend proof.
- Service replay protection is an in-memory bounded LRU. Restarting the Service
  loses it, allowing an old privileged command to execute again.
- Policy signatures cover policy content and session, but do not bind command ID,
  target Agent, command deadline, operation, or correlation ID. They therefore do
  not authorize `RESTORE_BASELINE` and are insufficient as command proof.

### Performance risks

- Agent and Gateway uplinks use bounded 1,000-item in-memory queues; overflow is
  explicit but Agent-originated messages have a volatile loss window before the
  Gateway's SQLite buffer accepts an Event.
- Backend command polling/routing and Gateway WebSocket sends are sequential per
  connection. A slow receiver can increase tail latency.
- Gateway durable-event flushing defaults to batches of 50, while the Backend
  PostgreSQL pool defaults to ten connections. These limits need measurement before
  tuning; Phase 9 will not introduce a broker or new topology without evidence.
- Redis writes publish synchronously after each presence update. Redis interruption
  degrades operational presence and must not affect PostgreSQL business truth.

### Stale documentation

- `README.md` still says the repository has no Local Gateway and that the Vite
  Docker image is broken, both contradicted by the Phase 8.1 validation.
- `docs/architecture-v1.0.md` labels the whole design as a Phase 0 target even though
  parts are implemented, while still presenting unimplemented anti-tamper,
  Agent-local durable queues, and large-scale targets without measured evidence.
- The original Phase 8 document reports Docker/PostgreSQL/Redis/TLS as blocked;
  Phase 8.1 subsequently validated them. The superseding status must be explicit.
- Phase 4 limitations about missing signature verification and persistent replay
  need historical context so readers do not mistake them for the final state.

### Target validation gaps

- This workstation is not an isolated elevated Windows validation target. Real SCM
  LocalSystem lifecycle, Named Pipe ACL enforcement, hosts/Firewall/process/USB
  mutation, kill-Client persistence, maintenance, and exact restore cleanup remain
  pending target validation.
- Production certificate ownership and executable/installer signing remain
  deployment concerns; ephemeral test TLS is not production PKI.
- Performance targets in the architecture document have not yet been measured by a
  repeatable harness. Results must be reported as measured, not inferred from unit
  or in-process integration tests.

The remaining sections of this document are completed after implementation and
measurement. Phase 9 preserves Protocol v2, the Local Gateway topology, Backend
authority, the Named Pipe boundary, and Service-owned enforcement.

## Completed hardening

- HTTP production-like authentication is prefix-aware for `/api/v1/*` and
  `/api/v2/gateways*`; `/health` remains intentionally anonymous. Router boundaries
  state RBAC permissions and Session scope.
- Backend HMAC authorization now covers command ID, operation, Session, target,
  policy hash, issued/deadline timestamps, and correlation ID. The Gateway routes
  it transparently and the Client cannot create it.
- The Service verifies command proof, target, expiry, and active Session before
  APPLY/RESTORE. Policy signatures remain a separate content authorization.
- A bounded atomic replay journal survives Service reconstruction, returns cached
  results for identical duplicate commands, and rejects conflicting duplicates.
- Gateway WebSocket child tasks and SQLite connections are deterministically closed.
  Disconnect presence reporting remains best-effort under explicit backpressure.
- The Gateway uplink queue remains bounded/configurable and fails explicitly. Its
  measured default changed from 1,000 to 4,096 after the former saturated during a
  500-Agent HELLO/ONLINE burst.

## Validation summary

The repeatable Phase 9 security suites cover unauthenticated operational paths,
wrong role/scope, forged/tampered/expired/wrong-session commands, policy proof,
and replay after Service recreation. Full Python regression, Ruff, frontend lint
and build are release gates. External PostgreSQL, Redis, Docker, and TLS/WSS status
is tracked by Phase 8.1 and the final run report.

Measured benchmark facts are in `phase-9-benchmark.md`: 500 Agents are the highest
repeatably stable control-plane result. Two 1,000-Agent attempts were inconsistent
(one reconnect lost 427 connections, one reached 1,000/1,000), so 1,000 is not
claimed stable. 100 is the highest stable Event workload and 250 Events timed out
at 180 seconds. These limits are not hidden or described as targets achieved.

## Release status

Implementation and cross-platform evidence can reach **READY FOR WINDOWS TARGET
VALIDATION**, not release-ready. An isolated elevated Windows VM must still execute
`scripts/windows/validate-release.ps1 -ConfirmIsolatedTestMachine` and validate SCM
LocalSystem, real Named Pipe ACL, hosts/Firewall/process/USB behavior, Client kill,
maintenance, recovery, and exact restore. Phase 10 is outside this document.

## Final evidence — 2026-09-28

- Full regression: 289 collected; 287 passed, two external-service tests skipped
  by default, ten warnings. The skipped PostgreSQL/Redis tests were then run against
  isolated PostgreSQL 17/Redis 7.4 containers and both passed.
- Phase 9 HTTP/command security plus Gateway tests: 27 passed. Named Pipe/Firewall
  targeted regression: 15 passed. Release vertical/reliability E2E: 11 passed on
  each of three consecutive runs.
- Ruff: passed. Frontend TypeScript lint and Vite production build: passed. NPM
  production dependency audit: zero known vulnerabilities after compatible lockfile
  updates.
- API, Gateway, and Web Docker images built from final source. Web/Nginx served the
  SPA in its expected `api` network context. Compose interpolation/config passed.
- Ephemeral trusted HTTPS returned 200; the same self-signed endpoint was rejected
  without its CA, and trusted WSS completed a real handshake. Temporary certificate
  material was removed after validation.
- The Windows validation script parses, but this host is not elevated or isolated.
  Real SCM/LocalSystem/OS mutation remains PENDING; the verdict is **READY FOR
  WINDOWS TARGET VALIDATION**, not release-ready.
