# Phase 9 — Measured benchmark

## Method

`scripts/benchmark/run.py` starts the production Gateway ASGI components on
loopback, a Protocol v2 Backend peer, simulated WSS Agents, and a temporary SQLite
Event buffer. PostgreSQL/Redis are deliberately outside the timed path. Results are
MEASURED on the development workstation on 2026-09-28; they are not universal
capacity guarantees. The project TARGET was to find a repeatable stable point and a
breaking point without changing topology.

Measured host: Windows 11 64-bit (build 10.0.26200), 8 physical/16 logical CPU
cores, 27.7 GiB RAM, Docker Desktop engine 27.3.1. Python allocation tracing was
enabled only for the separate 50-Agent memory run (30.071 MiB peak); it was disabled
for higher concurrency because its overhead materially changed timings.

## Results

| Agents | Scenario | Result | Success | P50 ms | P95 ms | P99 ms | Throughput/s | Event loss | Logical duplicates | Peak backlog | Notes |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 50 | connect | PASS | 100% | 255.329 | 277.369 | 279.554 | 178.189 | N/A | N/A | N/A | memory-traced all-scenario run |
| 50 | command | PASS | 100% | 52.506 | 88.513 | 91.557 | 500.651 | N/A | N/A | N/A | 50/50 ACK, wrong target 0 |
| 50 | Event | PASS | 100% | 1805.802 | 2478.371 | 2778.763 | 17.635 | 0 | 0 | N/A | SQLite durable path |
| 50 | WAN | PASS | 100% | N/A | N/A | N/A | 5.800 | 0 | 0 | 50 | flush 4.486 s; 23 delivery retries deduped |
| 100 | command | PASS | 100% | 74.986 | 122.212 | 125.725 | 739.646 | N/A | N/A | N/A | 100/100 ACK |
| 100 | reconnect | PASS | 100% | 347.542 | 395.683 | 398.004 | 250.728 | N/A | N/A | N/A | 100/100 |
| 100 | Event | PASS | 100% | 22730.973 | 25630.512 | 25785.389 | 3.871 | 0 | 0 | N/A | 100 accepted |
| 100 | WAN | PASS | 100% | N/A | N/A | N/A | 1.003 | 0 | 0 | 100 | 100 buffered/flushed; 95.588 s recovery; 2 delivery retries deduped |
| 250 | command | PASS | 100% | 114.193 | 223.121 | 230.575 | 1014.746 | N/A | N/A | N/A | 250/250 ACK |
| 250 | reconnect | PASS | 100% | 829.788 | 883.411 | 887.874 | 281.025 | N/A | N/A | N/A | 250/250 |
| 250 | Event | **TIMEOUT** | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | **BREAKING POINT:** exceeded 180 s; no PASS artifact |
| 500 | control, queue 1000 | **FAIL** | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 1000 | `QueueFull` during HELLO + ONLINE burst |
| 500 | command, queue 4096 | PASS | 100% | 172.386 | 335.966 | 348.461 | 1337.238 | N/A | N/A | 0 saturation | 500/500 ACK |
| 500 | reconnect, queue 4096 | PASS | 100% | 990.470 | 1239.580 | 1246.678 | 400.430 | N/A | N/A | 0 saturation | 500/500 |
| 1000 | command, attempt 1 | PASS | 100% | 747.945 | 1518.344 | 1613.147 | 500.678 | N/A | N/A | N/A | 1000/1000 ACK |
| 1000 | reconnect, attempt 1 | **FAIL** | 57.3% | 4009.828 | 4329.332 | 4356.447 | 131.332 | N/A | N/A | N/A | 573/1000; 427 connection failures |
| 1000 | command, attempt 2 | PASS | 100% | 362.388 | 590.891 | 620.587 | 1239.003 | N/A | N/A | 0 saturation | 1000/1000 ACK |
| 1000 | reconnect, attempt 2 | PASS | 100% | 2206.090 | 2547.086 | 2561.296 | 389.797 | N/A | N/A | 0 saturation | 1000/1000; result not consistent with attempt 1 |

The highest repeatably stable MEASURED control-plane load is conservatively 500
Agents. The two 1,000-Agent attempts disagreed, so 1,000 is not claimed stable. The
highest stable Event workload is 100 Agents/100 Events. The 250-Agent Event timeout
is retained as the measured breaking point. Inspection indicates the durable SQLite path's
per-event work/transactions dominate at this load. Phase 9 does not replace SQLite
or weaken at-least-once reliability merely to improve the number.

## Queue change

The original bounded Gateway Backend-uplink queue held 1,000 messages. A 500-Agent
reconnect burst generates at least HELLO plus ONLINE traffic and saturated it. The
measured project default is now 4,096 via
`EECP_GATEWAY_BACKEND_QUEUE_MAX_MESSAGES`. It remains bounded, validates positive,
and `send()` raises an explicit failure when full; important messages are never
silently dropped. A focused unit test forces overflow with a size-one queue. 4,096
is not claimed to be universally optimal.

## Soak and reproduction

The automated short soak command is:

```powershell
uv run python scripts/benchmark/run.py --agents 50 --scenario soak --duration 120 --timeout 180 --output artifacts/phase9-benchmark-50-soak.json
```

It completed 28 presence/Event cycles: 1,400 Events sent and accepted, zero loss,
zero logical duplicates, zero queue saturations, and zero wrong-target deliveries.
Connections remained usable and each Event iteration drained before the next one;
no stuck Gateway buffer was observed. Python allocation tracing was intentionally
off for this timing run because its overhead had already been measured separately.

For a later manual 30–60 minute host soak, use `--duration 1800` or `3600` and
monitor process memory, queue saturation logs, connected Agents, and SQLite pending
count. Artifacts in `artifacts/phase9-benchmark-*.json` are the source of the table.

## Limitations

Loopback removes real LAN/WAN latency, TLS is disabled in the timed harness, the
Backend is a protocol peer, and simulated Agents do not measure Windows OS mutation.
Use this benchmark to identify this implementation's bottlenecks, not as a hardware-
independent service-level guarantee.
