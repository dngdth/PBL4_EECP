# Exam Environment Control Platform (EECP)

EECP is a Protocol v2 exam-control platform with a central FastAPI Backend, a
room-local Gateway, a normal-user Agent Client, and a privileged Windows Agent
Service. The web application is React/Vite and is served by Nginx in its container.

## Current architecture

```text
Examiner Dashboard
        |
        v
Central Backend ---- PostgreSQL (business truth)
        |          `- Redis (ephemeral presence)
       WSS
        v
Local Gateway ----- SQLite durable Event buffer
        |
       WSS
        v
Agent Client ------- sensors (process/network observations)
        |
   Named Pipe
        v
Agent Service ------ hosts + Windows Firewall + process + USB enforcement
```

The Backend is authoritative for identities, sessions, policy, commands, ACKs,
incidents, and audit. The Gateway routes commands unchanged and durably buffers
Events during WAN loss. Production Agent control uses Gateway WSS; direct Backend
polling exists only as explicit compatibility mode. The normal-user Client owns
sensors and transport, while the LocalSystem Service alone owns OS mutation and
maintenance.

Authentication and RBAC are enforced by the Backend. Gateway and Agent WebSocket
peers use identity-bound machine credentials. Policies and privileged commands
carry Backend HMAC proofs which the Service verifies independently; the Client and
Gateway do not possess signing keys. Named Pipe ACLs are local transport controls,
not proof of Backend command authority.

## Repository layout

```text
apps/api/          FastAPI Backend, PostgreSQL/SQLite adapters, Redis presence
apps/gateway/      Local Gateway, WSS routing, SQLite Event buffer
apps/web/          React/Vite Dashboard
agent/client/      normal-user transport and sensors
agent/service/     privileged Service and maintenance loop
agent/ipc/         local Named Pipe protocol/transport
agent/infrastructure/ Windows enforcement adapters
contracts/v2/      shared Protocol v2 contracts
docs/v2/           phase and release documentation
scripts/benchmark/ repeatable loopback Gateway benchmark
scripts/windows/   Windows service/target validation helpers
```

## Development

```powershell
uv sync --all-packages
uv run --package eecp-api uvicorn app.main:app --app-dir apps/api --reload

Set-Location apps/web
npm install
npm run dev
```

Run the Local Gateway with the required `EECP_GATEWAY_*` configuration:

```powershell
uv run python -m apps.gateway.app.main
```

The Agent Client runs as a normal user and connects to the Local Gateway. The
Agent Service is installed separately under Windows SCM/LocalSystem; do not run
the Client as Administrator to compensate for a missing Service. See
[`docs/v2/final-demo-guide.md`](docs/v2/final-demo-guide.md).

## Production-like Docker stack

Copy `.env.example` to a protected local `.env`, replace every required secret and
certificate path, then run `docker compose up --build`. The stack contains
PostgreSQL 17, Redis 7.4, migration, API, and Vite/Nginx web services. Enable the
`gateway` profile for the Local Gateway. Phase 8.1 validated the images and TLS/WSS
with isolated test certificates; production requires a real CA and protected
secrets. See [`docs/v2/phase-8-release-validation.md`](docs/v2/phase-8-release-validation.md).

## Verification

```powershell
uv run pytest
uv run ruff check .
Set-Location apps/web
npm run lint
npm run build
```

See [`docs/v2/phase-9-benchmark.md`](docs/v2/phase-9-benchmark.md) for measured
capacity. Real SCM, LocalSystem, Named Pipe ACL, Firewall, hosts, process, USB,
kill-Client, and restore validation remains mandatory on an isolated elevated
Windows VM before release.
