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
