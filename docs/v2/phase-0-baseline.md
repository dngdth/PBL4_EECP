# EECP v2 — Phase 0 Baseline

Baseline captured on 2026-09-28 from code at commit
`ad3d5ea70d1a782dcdf3a740d763c6a80726a6c8`.

## 1. Repository state

- Branch: `dev` (tracking `origin/dev`).
- Commit: `ad3d5ea70d1a782dcdf3a740d763c6a80726a6c8` (`fix: API connect of FE`).
- Initial working tree status: clean; no modified or untracked files.
- Phase 0 working tree after this review: documentation-only changes to `README.md`,
  `docs/clean-architecture-structure.md`, and `docs/architecture-v1.0.md`, plus this report.
- Main directories: `agent/`, `apps/api/`, `apps/web/`, `data/`, and `docs/`.
- Entrypoints: backend `apps/api/app/main.py`, Agent `agent/main.py`, frontend
  `apps/web/src/main.tsx` with `apps/web/index.html` and Vite.
- No baseline branch or tag was created because the Phase 0 documentation changes
  are uncommitted and must be reviewed first.

## 2. Current architecture

```text
React 19 dashboard (Vite, browser)
              |
              | REST /api/v1 (Vite dev proxy)
              v
FastAPI central backend
              |
              +-- application use cases and domain model
              +-- SQLite repositories / Unit of Work
              +-- synchronous command queue, ACK, audit and telemetry handling
              ^
              |
              | direct HTTP register / heartbeat / polling / ACK / telemetry
              |
Single Python Agent process
              +-- AgentClient
              +-- PolicyCommandProcessor
              +-- AuditPolicyEnforcer or WindowsPolicyEnforcer
              +-- BlockedDomainMonitor threads
```

There is no Local Gateway process. The legacy pipeline stores a `gateway_id` and
queues commands to it as a logical target, which the demo script simulates by
polling and acknowledging through the same HTTP endpoints used by Agents.

The backend follows domain/application/infrastructure/presentation boundaries.
FastAPI constructs a container at startup, initializes SQLite, and serves routers
for Agents, sessions/pipeline operations, policy profiles, commands/ACK, telemetry,
summary, and health.

## 3. Current backend capabilities

- Idempotent Agent registration, five-second Agent heartbeat loop, and 15-second
  liveness classification when Agents are listed or session details are built.
- Direct management sessions with Agent assignment, online/conflict readiness
  gates, policy snapshots, and `CREATED -> READY -> RUNNING -> FINISHED` lifecycle.
- A legacy pipeline session mode with `gateway_id`, explicit policy deployment,
  command ACK, preflight, start/force-start, telemetry, incident correlation,
  finish, restore ACK, summary, and audit-chain verification.
- Built-in and custom policy profile list/create/update/delete, including immutable
  built-ins and in-use deletion protection.
- Durable SQLite command queue with delivery attempts, ten-second retry spacing,
  maximum three attempts, one-minute expiry, timeout/failure recording, and ACK.
- SHA-256 policy hashes and policy versions. The backend validates an
  `APPLY_POLICY` ACK hash against the session's desired hash.
- Persistent audit events linked by a hash chain.

### Current business flow and owners

| Step | Current implementation |
| --- | --- |
| Agent register | `agent.application.runtime.run_agent` calls `AgentClient.register`; `presentation/api/routers/agents.py::register_agent` delegates to `RegisterAgent`. |
| Heartbeat | `run_agent` calls `AgentClient.heartbeat`; `heartbeat_agent` delegates to `HeartbeatAgent`. A failed request resets local registration state. |
| Create session | `exam_sessions.py::create_session` selects `CreateExamSession` for direct management or `ExamPipelineService.create_session` for legacy pipeline requests. |
| Bind participant/workstation | Workstations/Agents are assigned through `CreateExamSession` and `session_workstations`; participant/student identity binding is **NOT IMPLEMENTED**. |
| Deploy policy | Direct sessions assign a profile snapshot and enqueue `APPLY_POLICY` during `CreateExamSession`; legacy sessions use `ExamPipelineService.deploy_policy`. |
| Receive command | `PolicyCommandProcessor.process_pending` calls `AgentClient.pending_commands`; backend `GetPendingCommands` marks available commands delivered or timed out. |
| Process command | `PolicyCommandProcessor._execute` accepts `APPLY_POLICY` and `RESTORE_BASELINE`. |
| Enforce | `AuditPolicyEnforcer` only validates/persists state; `WindowsPolicyEnforcer.apply` changes hosts, USB registry state, and denied processes. |
| ACK | `AgentClient.acknowledge_command` posts to the ACK route; `AcknowledgeCommand` persists success/failure and updates session policy/restore state. |
| Run session | Direct mode transitions by status PATCH; legacy mode starts after preflight through `ExamPipelineService.start_session`. |
| Finish | Direct mode PATCH to `FINISHED`; legacy mode uses `/finish` and enters `RESTORING`. Both enqueue restore commands. |
| Restore | Agent executes `WindowsPolicyEnforcer.restore` or audit restore, then ACKs. Direct sessions remain `FINISHED` and expose per-Agent restored state; legacy sessions become `NORMAL` after gateway and all workstations ACK. |

## 4. Current Agent architecture

- One foreground Python process started with `python -m agent.main`; it is not a
  Windows Service and is not split into user and privileged processes.
- `AgentClient` uses Python `urllib` for direct JSON/HTTP calls to the central backend.
- `run_agent` registers once, then heartbeats and runs the command/control cycle
  every five seconds. Network `OSError` causes re-registration on the next cycle.
- `PolicyCommandProcessor` polls, applies/restores, sends ACK, then calls
  `enforcer.maintain()` once per control cycle.
- `BlockedDomainMonitor` starts daemon listeners on loopback ports 80 and 443,
  extracts HTTP Host or TLS SNI for hosts-redirected requests, debounces reports,
  and sends policy-violation telemetry. Failure to bind is logged and does not stop
  the Agent.
- Default `EECP_POLICY_MODE` is `enforce`; explicit `audit` mode performs no OS
  enforcement. `EECP_AGENT_ID` is mandatory.

## 5. Current enforcement

| Mechanism | Implemented | File | Notes |
| --- | --- | --- | --- |
| hosts | YES | `agent/infrastructure/policy_enforcement.py` | Rewrites only an EECP-marked block to loopback and flushes DNS. It does not keep a separate full hosts backup; restoration removes the managed block while retaining other content. |
| denied process | YES | `agent/infrastructure/policy_enforcement.py` | Uses `taskkill /F /IM`; applied immediately and repeated by `maintain()`. |
| USB | YES | `agent/infrastructure/policy_enforcement.py` | Reads and stores the prior `HKLM\\...\\USBSTOR\\Start` DWORD, sets it to `4`, and restores the stored value (fallback `3` only if state lacks a valid prior integer). |
| registry | PARTIAL | `agent/infrastructure/policy_enforcement.py` | Registry enforcement is limited to the USBSTOR service start value. |
| network/domain | YES, DOMAIN ONLY | `policy_enforcement.py`, `violation_monitor.py` | Static category domains are redirected through hosts. Direct-IP blocking is not implemented. |
| Firewall | **NOT IMPLEMENTED** | — | No Windows Firewall, WFP, iptables, or network-driver enforcement. |
| baseline state | YES | `agent/infrastructure/policy_enforcement.py` | Atomic JSON state stores mode, hash/version/profile, denied apps, domains, USB flag, and the previous USB value when applicable. |
| restore | YES | `agent/infrastructure/policy_enforcement.py` | Removes the managed hosts block, restores USB when it was denied, and deletes the state file. Repeated restore is code-path idempotent for hosts/state; without saved state it does not change USB. |
| maintain loop | PARTIAL | `policy_commands.py`, `runtime.py`, `main.py` | Called after each pending-command poll. It re-terminates denied processes only; it does not reassert hosts or USB state. |

Real enforcement requires Windows permissions sufficient to edit the system hosts
file and HKLM; error messages explicitly direct the operator to run the Agent as
Administrator. Binding the loopback monitor to ports 80/443 can also be unavailable
if those ports are reserved or occupied.

## 6. Current transport

| Interaction | Transport |
| --- | --- |
| Dashboard to backend | REST/JSON; relative `/api/v1`, with Vite development proxy to `127.0.0.1:8000` |
| Agent registration | HTTP POST `/api/v1/agents/register` |
| Heartbeat | HTTP POST `/api/v1/agents/{id}/heartbeat` |
| Command receive | HTTP GET polling `/api/v1/agents/{id}/commands` after each heartbeat/control cycle |
| ACK | HTTP POST `/api/v1/commands/{id}/acknowledge` |
| Violation telemetry | HTTP POST `/api/v1/sessions/{id}/telemetry` |

There is no WebSocket, Named Pipe, message broker, or Agent-to-Gateway transport.

## 7. Data storage

The only application database is SQLite, defaulting to `./data/eecp.db` and
configurable with `EECP_DATABASE_PATH`. Schema initialization and the small command
delivery column migration are idempotent. Sessions are stored as JSON aggregates;
Agents, assignments, commands, profiles, telemetry, incidents, and audit events have
SQLite tables. PostgreSQL and Redis are **NOT IMPLEMENTED**.

## 8. Frontend

The frontend is React 19 with TypeScript, React Router, Vite 6, and Tailwind CSS 4.
Its entrypoint is `apps/web/src/main.tsx`; feature-specific REST clients call the
relative `/api/v1` base. It is not Next.js. The current web Dockerfile is stale: it
builds Vite but its runner stage copies `.next` output, so the Compose web image is
not a verified deployment path.

## 9. Tests executed

| Command | Result | Notes |
| --- | --- | --- |
| `uv sync --all-packages` | PASS | 33 packages resolved; 31 checked. |
| `uv run ruff check apps/api agent` | PASS | No lint violations. This includes Agent code in addition to the requested backend path. |
| `uv run pytest` | PASS | 102 tests passed; 9 dependency deprecation warnings. Unit and integration tests include Agent client, command processor, enforcement adapters, APIs, SQLite, retry/ACK, lifecycle, and architecture boundaries. |
| `npm install` in `apps/web` | PASS WITH WARNING | Up to date; audit reported 3 moderate vulnerabilities and npm reported pending install-script approvals. No automatic audit fix was run. |
| `npm run lint` in `apps/web` | PASS | Runs `tsc --noEmit`. |
| `npm run build` in `apps/web` | PASS | TypeScript build and Vite production bundle succeeded. |
| `docker compose config --quiet` | PASS | Compose YAML is structurally valid; this does not build the stale web image. |
| Uvicorn `/health` with temporary SQLite DB | PASS | Backend started on loopback and returned `{"status":"ok"}`. Temporary DB was removed after shutdown. |
| `demo_pipeline.py --base-url http://127.0.0.1:8765` | PASS | API simulator completed create/deploy/ACK/preflight/start/telemetry/finish/restore/summary and reported a valid audit chain. This did not execute the real Agent or OS controls. |
| Real Windows enforcement | BLOCKED | Not run because it would modify the host hosts file/HKLM, kill named processes, and requires an isolated elevated Windows test machine. |
| `docker compose up --build` | BLOCKED | Not run as a passing baseline because the inspected web Dockerfile expects obsolete `.next` artifacts instead of Vite `dist`. |

## 10. Manual regression checklist

- [ ] BLOCKED — Start an isolated Windows VM and run PowerShell as Administrator.
- [ ] BLOCKED — Agent register against the target backend from the VM.
- [ ] BLOCKED — Heartbeat and offline/online recovery on the target LAN.
- [x] PASS — Create session through automated API/integration coverage.
- [x] PASS — Deploy policy through automated API/integration coverage and simulator.
- [x] PASS — Receive command through automated Agent-client/unit coverage and simulator.
- [x] PASS — Apply policy contract/hash in audit/unit coverage; real OS apply is BLOCKED.
- [x] PASS — ACK and backend policy status/hash through integration coverage and simulator.
- [ ] BLOCKED — Confirm hosts block insertion, DNS flush, preservation of non-EECP lines, and removal after restore on the VM.
- [ ] BLOCKED — Launch each denied process and confirm immediate plus five-second-cycle termination on the VM.
- [ ] BLOCKED — Record USBSTOR `Start`, apply deny, confirm value `4`, restore, and confirm the exact prior value on the VM.
- [x] PASS — Finish and restore command/ACK lifecycle through integration coverage and simulator.
- [ ] BLOCKED — Invoke real restore twice and confirm hosts/state remain clean and USB baseline remains unchanged on the VM.

## 11. Known gaps before EECP v2

- Agent is not split into a user Client and privileged Windows Service.
- Named Pipe IPC and a privileged execution port are not implemented.
- No Local Gateway service exists; legacy `gateway_id` is only a logical command target.
- Domain blocking is hosts-based only; direct-IP and Windows Firewall enforcement are absent.
- `maintain()` only re-kills denied processes; it does not reassert hosts/USB controls.
- PostgreSQL, Redis, a message broker, and distributed command delivery are absent.
- Authentication and RBAC are absent from the HTTP API.
- Participant/student binding is absent; sessions bind workstation Agent IDs only.
- Real elevated Windows enforcement remains to be validated on an isolated VM.
- The web Dockerfile/Compose path is stale after the frontend's migration to Vite.
- `npm install` currently reports three moderate dependency vulnerabilities.

These gaps are recorded only; Phase 0 does not implement them.

## 12. Phase 0 conclusion

**READY FOR PHASE 1**

The current local development baseline is reproducible: backend and Agent lint,
all automated tests, frontend type-check/build, backend startup, and the complete API
simulator flow pass. No application or enforcement behavior was changed. Real
elevated Windows enforcement and the stale container deployment remain explicitly
blocked/non-baselined and must not be represented as passing.

After reviewing and committing these documentation-only Phase 0 changes, create a
baseline without overwriting any existing reference, for example:

```powershell
git branch baseline/eecp-v1-before-v2
# or
git tag eecp-v1-baseline
```

Do not create either reference until the working tree is clean and the intended
Phase 0 commit has been selected.
