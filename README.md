# Exam Environment Control Platform (EECP)

Monorepo tách rõ backend FastAPI và frontend Next.js, tổ chức theo Clean Architecture.

```text
PBL4_EECP/
├── apps/
│   ├── api/                    # Backend FastAPI
│   │   ├── app/
│   │   │   ├── domain/         # Business rules và output ports
│   │   │   ├── application/    # DTO + use cases
│   │   │   ├── infrastructure/ # SQLite, repository, DI
│   │   │   ├── presentation/   # FastAPI routers + HTTP schemas
│   │   │   ├── config.py
│   │   │   └── main.py
│   │   ├── scripts/
│   │   ├── test/
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   └── web/                    # Frontend Next.js, feature-first
│       ├── app/
│       ├── features/
│       ├── components/
│       ├── lib/
│       ├── Dockerfile
│       └── package.json
├── docs/
├── compose.yaml
├── pyproject.toml              # Python workspace/tooling
└── README.md
```

Không có `worker/` vì pipeline hiện tại được xử lý đồng bộ trong API và chưa có background job đủ độc lập để tách service.

## Pipeline đã triển khai

`Create → Deploy Policy → Agent/Gateway ACK → Preflight → Start → Telemetry/Incident → Finish → Restore → Summary`

- State machine: `CREATED → DEPLOYING → PREFLIGHT → READY/DEGRADED → RUNNING → RESTORING → NORMAL`.
- Policy có version và SHA-256 hash.
- Agent/Gateway poll command và ACK đúng policy hash.
- Preflight tính `READY/WARNING/FAILED` từ critical/non-critical check.
- Ba lỗi DNS trên ba máy được gom thành incident `INFRASTRUCTURE_DNS`.
- Blocked event không tự động được coi là bằng chứng gian lận.
- Audit event dùng hash-chain và được verify trong summary.

Chi tiết dependency rule và vị trí đặt code mới: [docs/clean-architecture-structure.md](docs/clean-architecture-structure.md).

## Chạy backend

```powershell
uv sync --all-packages
uv run --package eecp-api uvicorn app.main:app --app-dir apps/api --reload
```

- API docs: <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/health>

Chạy demo pipeline khi API đang hoạt động:

```powershell
uv run --package eecp-api python apps/api/scripts/demo_pipeline.py
```

## Chạy frontend

```powershell
cd apps/web
npm install
npm run dev
```

Frontend: <http://127.0.0.1:3000>

## Chạy bằng Docker

```powershell
docker compose up --build
```

## Kiểm tra

```powershell
uv run ruff check apps/api
uv run pytest

cd apps/web
npm run typecheck
npm run build
```

## Demo Phase 1: Agent registration and heartbeat on LAN

### 1. Start the Control Server at 192.168.3.50

From the repository root:

```powershell
uv sync --all-packages
uv run --package eecp-api uvicorn app.main:app --app-dir apps/api --host 0.0.0.0 --port 8000
```

In another PowerShell terminal:

```powershell
$env:EECP_API_URL="http://192.168.3.50:8000"
cd apps/web
npm install
npm run dev -- --hostname 0.0.0.0
```

Allow inbound TCP ports 8000 and 3000 in Windows Firewall when other LAN machines cannot connect.

### 2. Start Workstation A as PC01

Clone or copy this repository to Workstation A, open PowerShell at the repository root, then run:

`EECP_AGENT_ID` is required and has no default. The agent exits before registration
when the variable is missing or blank.

```powershell
$env:EECP_SERVER_URL="http://192.168.3.50:8000"
$env:EECP_AGENT_ID="PC01"
Write-Host $env:EECP_AGENT_ID
python -m agent.main
```

Expected workstation address for the demo: `192.168.3.56`.

### 3. Start Workstation B as PC02

Clone or copy this repository to Workstation B, open PowerShell at the repository root, then run:

```powershell
$env:EECP_SERVER_URL="http://192.168.3.50:8000"
$env:EECP_AGENT_ID="PC02"
Write-Host $env:EECP_AGENT_ID
python -m agent.main
```

Expected workstation address for the demo: `192.168.3.55`.

### 4. Run the LAN demo

1. Open `http://192.168.3.50:3000/workstations` on the Control Server or another LAN machine.
2. Verify PC01 and PC02 both show `ONLINE`.
3. Stop one workstation agent with Ctrl+C.
4. Wait up to 20 seconds for the 15-second timeout plus the next dashboard refresh.
5. Verify the stopped workstation shows `OFFLINE` while the other remains `ONLINE`.
6. Restart the stopped agent and verify it returns to `ONLINE` without creating a duplicate record.

## Demo Phase 2: Exam session management on LAN

Phase 2 adds teacher-managed sessions alongside the existing policy pipeline. The
management lifecycle is `CREATED -> READY -> RUNNING -> FINISHED`. The legacy
pipeline endpoints (policy deploy, command ACK, preflight, start, telemetry,
finish, and summary) remain available under `/api/v1`; existing pipeline clients
continue to use their original request shape with `exam_name`, `room_id`,
`gateway_id`, and `workstation_ids`.

### 1. Start (or restart) the Control Server

From the repository root on the Control Server (`192.168.3.50`):

```powershell
uv sync --all-packages
uv run --package eecp-api uvicorn app.main:app --app-dir apps/api --host 0.0.0.0 --port 8000
```

On every server start, the SQLite initialization runs its idempotent schema.
That creates `session_workstations` for an existing database when it is absent;
it does not delete existing sessions or assignments. The table stores only the
assignment (`id`, `session_id`, `agent_id`, `assigned_at`), enforces unique
`(session_id, agent_id)` pairs, and references both `exam_sessions` and
`agents`.

In a second PowerShell terminal, start the frontend:

```powershell
$env:EECP_API_URL="http://192.168.3.50:8000"
Set-Location apps/web
npm install
npm run dev -- --hostname 0.0.0.0
```

Open the teacher workflow at `http://192.168.3.50:3000/sessions/create` and
the session dashboard at `http://192.168.3.50:3000/sessions`.

### 2. Start the required Agents with distinct IDs

Use separate workstation PowerShell sessions. `EECP_AGENT_ID` is required and
must be different for every workstation; do not run both agents with the same
ID.

On PC01 (`192.168.3.56`):

```powershell
$env:EECP_SERVER_URL="http://192.168.3.50:8000"
$env:EECP_AGENT_ID="PC01"
python -m agent.main
```

On PC02 (`192.168.3.55`):

```powershell
$env:EECP_SERVER_URL="http://192.168.3.50:8000"
$env:EECP_AGENT_ID="PC02"
python -m agent.main
```

Before creating a managed session, verify both appear as `ONLINE` at
`http://192.168.3.50:3000/workstations` (or query `GET /api/v1/agents`).

### 3. Management API example

The following PowerShell example creates a management session, lists sessions,
gets its detail, then transitions it through the supported lifecycle:

```powershell
$apiBase="http://192.168.3.50:8000"
$createBody=@{
  name="PBL4 Final"
  room="A101"
  agent_ids=@("PC01", "PC02")
  actor="teacher"
} | ConvertTo-Json

$session=Invoke-RestMethod -Method Post -Uri "$apiBase/api/v1/sessions" -ContentType "application/json" -Body $createBody
Invoke-RestMethod -Method Get -Uri "$apiBase/api/v1/sessions"
Invoke-RestMethod -Method Get -Uri "$apiBase/api/v1/sessions/$($session.id)"

foreach ($status in "READY", "RUNNING", "FINISHED") {
  $statusBody=@{ status=$status; actor="teacher" } | ConvertTo-Json
  Invoke-RestMethod -Method Patch -Uri "$apiBase/api/v1/sessions/$($session.id)/status" -ContentType "application/json" -Body $statusBody
}
```

Before running the transition loop, wait until every assigned Agent shows
`POLICY APPLIED`. Phase 3.1 rejects `CREATED -> READY` while any policy is still
`PENDING` or `FAILED`.

The `POST /api/v1/sessions` example above accepts management fields `name`,
`room`, and `agent_ids`; the API returns `201 Created`. `GET /api/v1/sessions`
lists sessions, `GET /api/v1/sessions/{session_id}` returns a session and its
assigned Agent details, and `PATCH /api/v1/sessions/{session_id}/status`
advances exactly one lifecycle state at a time.

### 4. Offline readiness gate and live demo sequence

An Agent that is `OFFLINE` is rejected at management-session creation with
`409 Conflict` and code `READINESS_GATE`. A selected Agent that goes offline
after creation also blocks `CREATED -> READY` with the same `409` and code;
restart the Agent, wait for it to become `ONLINE`, then retry the `READY`
transition. `RUNNING` is allowed only after `READY`, and `FINISHED` only after
`RUNNING`; skipped or invalid transitions return `409 Conflict`.

For the LAN demo:

1. Confirm PC01 and PC02 are `ONLINE` on `/workstations`.
2. Open `/sessions/create`, enter an exam name and room, select PC01 and PC02, then create the session (`CREATED`).
3. Open `/sessions`, wait until both assigned Agents show `POLICY APPLIED`, then transition to `READY`.
4. Transition to `RUNNING`, then to `FINISHED`.
5. To demonstrate the gate, stop either Agent with Ctrl+C, wait up to 20 seconds for offline detection, and attempt either a new creation or the `READY` transition; verify the `READINESS_GATE` response. Restart that Agent before continuing.

## Demo Phase 3: Policy management

Every new direct-management session now receives a policy profile. The Control
Server renders the profile as YAML, hashes the canonical policy, and queues one
`APPLY_POLICY` command per assigned Agent in the same transaction as session
creation. The Agent checks pending commands after each heartbeat and reports an
acknowledgement. The sessions dashboard shows `POLICY PENDING`, `POLICY APPLIED`,
`POLICY FAILED`, or `POLICY RESTORED` for each workstation.

Phase 3.1 retries an unacknowledged command after 10 seconds, up to three
delivery attempts and within a one-minute lifetime. A timed-out command marks
that Agent's policy as `FAILED`. `READY` requires all Agents to be online with
`POLICY APPLIED`, and the API prevents the same Agent from being assigned to
overlapping active sessions (`409 SESSION_CONFLICT`).

Phase 3.2 adds persistent teacher-managed profiles. Open
`http://192.168.3.50:3000/policies` (or select **Manage policies** on the
sessions page) to create a custom profile using application, network-category,
and USB controls. Custom profiles immediately appear on `/sessions/create`.
Editing a profile applies only to future sessions because each session keeps a
policy snapshot and hash. Built-in profiles are read-only, and a custom profile
already referenced by a session cannot be deleted (`409 POLICY_IN_USE`).

The built-in profiles are available from `GET /api/v1/policy-profiles`. The
default `INTERNET_NO_AI` profile allows programming tools, blocks known
generative-AI and social-network domains, terminates explicitly denied remote
control/AI applications, and denies USB storage. The browser itself is not
blocked: AI access is controlled by the network category, so ordinary Internet
resources remain usable.

### Run the Windows Agent

The default mode applies real Windows controls and therefore PowerShell must be
opened with **Run as Administrator**:

```powershell
$env:EECP_SERVER_URL="http://192.168.3.50:8000"
$env:EECP_AGENT_ID="PC01"
$env:EECP_POLICY_MODE="enforce"
python -m agent.main
```

`enforce` performs three reversible controls:

- denied applications are terminated and checked again after each heartbeat;
- category domains are placed inside an EECP-marked block in the Windows hosts file;
- USB mass storage is disabled through the Windows `USBSTOR` service setting.

When the teacher moves a management session to `FINISHED`, the server queues a
`RESTORE_BASELINE` command. The Agent removes only the EECP hosts block, restores
the previous USB setting, deletes its local policy state, and acknowledges the
restore. Do not delete the policy state file manually while a policy is active,
because it contains the previous USB value needed for restoration.

For a UI/API demo that must not modify Windows, explicitly use audit mode. It
validates and records the policy locally but does not enforce OS controls:

```powershell
$env:EECP_POLICY_MODE="audit"
python -m agent.main
```

The state path defaults to `%LOCALAPPDATA%\EECP\policy-state.json` and can be
overridden with `EECP_POLICY_STATE_PATH`. More implementation and API details
are in [docs/policy-management.md](docs/policy-management.md).

