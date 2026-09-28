# Phase 9 benchmark harness

This non-destructive harness starts the real Local Gateway ASGI application on
ephemeral loopback ports, a Protocol v2 benchmark Backend peer, and simulated Agent
WebSocket clients. It uses a temporary Gateway SQLite event buffer and deletes it on
exit. It does not modify Windows policy or production databases.

```powershell
uv run python scripts/benchmark/run.py --agents 100 --output artifacts/benchmark-100.json
uv run python scripts/benchmark/run.py --agents 500 --scenario command
uv run python scripts/benchmark/run.py --agents 500 --scenario command --queue-max-messages 4096
uv run python scripts/benchmark/run.py --agents 500 --scenario wan
uv run python scripts/benchmark/run.py --agents 100 --scenario soak --duration 180
uv run python scripts/benchmark/run.py --agents 50 --measure-memory
```

`all` covers connection setup, presence, exact-target command/ACK routing, Event
ingest, WAN buffering/flush, and reconnect. The timed Backend is intentionally a
protocol peer, so PostgreSQL/Redis results must be measured separately with the
external-service integration suite. The default soak is two minutes; use 1,800–3,600
seconds for a manual release-host soak.
Python allocation tracing is opt-in because it materially distorts high-concurrency
SQLite/WebSocket timings; use `--measure-memory` as a separate diagnostic run.
