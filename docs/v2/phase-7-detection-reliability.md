# Phase 7 — Detection + Reliability

## 1. Goals

Phase 7 separates observation from privileged enforcement, makes important Agent
events durable across Backend/WAN and Gateway restarts, deduplicates logical
processing by stable `event_id`, and defines transport presence consistently.

## 2. Sensor vs Actuator

```text
Agent Client Sensor -> Event -> Gateway SQLite buffer -> Backend

Agent Client Command -> Named Pipe -> Agent Service -> Enforcement
```

Sensors observe and report. They never edit hosts, Firewall, HKLM, or terminate
privileged processes. The Service remains the actuator and never connects to the
Gateway or Backend.

## 3. Sensors

- Process sensor: reports a denied process on absent → present transitions.
- Domain sensor: retains the existing HTTP/TLS loopback observation and cooldown.
- Service health: periodically uses the existing Named Pipe HEALTH_CHECK.
- Policy integrity: compares trusted active-policy state with Service health hash.
- USB/device detection is not added because there is no trustworthy observation
  source in the current code.
- Direct-IP prevention exists, but direct-IP attempt detection is not claimed.

## 4. Event contract

Protocol v2 `Event` remains the only event DTO. UUID-based `event_id` is the
dedupe identity. Agent-local sequence is diagnostic only and may reset after a
Client restart. `occurred_at` is preserved; arrival time is not a dedupe key.
Payloads contain only the minimal process name, domain, health state, or hashes.

## 5. Delivery semantics

Network delivery is **AT-LEAST-ONCE**. Backend logical processing is
**IDEMPOTENT BY event_id**. Exactly-once delivery is not claimed.

## 6. Gateway durable buffer

Every validated important Event is inserted into a Gateway-local SQLite database
before any Backend send. Presence, heartbeat, commands, and command ACKs do not
enter this queue. The schema records event identity and envelope, state,
attempt count, creation/occurrence times, next attempt, and last error. One
Gateway process owns the database; each operation uses a bounded transaction,
WAL, busy timeout, and an `event_id` primary key.

## 7. Event receipt

Command ACK and event receipt remain distinct Protocol v2 contracts. Backend
returns `ACCEPTED`, `ALREADY_PROCESSED`, or terminal `REJECTED`. Only accepted or
already-processed receipts remove a buffered event.

## 8. Retry/backoff

One background flush worker claims due rows and uses capped exponential backoff
plus jitter. A WebSocket send is not delivery confirmation. Missing receipts
leave the row retryable with the same `event_id`.

## 9. Crash recovery

Pending and expired in-flight rows are read from SQLite after Gateway restart.
The worker resumes when the Backend uplink is online. Confirmed rows are deleted;
terminal rejects remain for diagnostics and affect health.

## 10. Backend dedupe

Backend persists the original `event_id` as the telemetry primary key. A retry
finds that persistent row before incident/audit evaluation and returns
`ALREADY_PROCESSED`; it cannot create a second logical Incident or audit record.

## 11. Presence

Connected plus recent valid signal is `ONLINE`; connected but stale, Service
unavailable, policy mismatch, or high Gateway backlog is `DEGRADED`; disconnect
or the configured offline timeout is `OFFLINE`. Reconnect/reannounce restores
`ONLINE` when health is otherwise good. Presence memory is rebuilt after restart.

## 12. Presence vs violation

Presence is ephemeral transport/health state. `OFFLINE` is never itself cheating
evidence and does not create a cheating Incident.

## 13. Buffer limits

The row limit and degraded threshold are configurable. At the hard limit the
Gateway rejects/backpressures a new event rather than silently acknowledging a
drop. Existing important rows are retained and health becomes `DEGRADED`.

## 14. Failure handling

Network and temporary failures retain rows for retry. Backend terminal rejection
marks a row terminal with its diagnostic error and stops rapid retries. A sensor
exception is logged without stopping other sensors or the Agent loop.

## 15. Tests

The Phase 7 tests cover process transition/debounce, Service unavailable and
recovered transitions, policy-hash mismatch, WAN outage and automatic recovery,
SQLite restart, lost receipts, transient retry, terminal rejection, hard buffer
limits, persistent Backend deduplication, out-of-order occurrence timestamps,
and presence transitions. The final local regression result is **240 passed,
9 warnings**. Ruff, frontend type-check/lint, and frontend production build pass.

Focused regressions also pass for Gateway routing, Named Pipe transport, Phase 6
full-vertical behavior, Windows Firewall fakes, and architecture boundaries.

## 16. Known limitations

- Loss or corruption of the Gateway's local disk can lose its SQLite buffer.
- The Agent has no local durable event queue, so a failure before the Gateway
  confirms its durable insert remains a loss window.
- Agent/Gateway authentication remains provisional; full credentials/PKI are a
  Phase 8 concern.
- Policy signature verification remains unimplemented.
- Presence is intentionally in memory and is rebuilt from reconnect/reannounce
  after a Gateway restart.
- Real elevated Windows enforcement was not rerun because this workstation is
  not an isolated elevated validation VM; unit/integration fakes cover the flow.
- USB/device and direct-IP attempt sensors are not implemented because the
  current system has no reliable observation source for them.

Production defaults use `%LOCALAPPDATA%\EECP\gateway-events.db`, a 10,000-row
hard limit, an 8,000-row degraded threshold, retry delays from 1 to 30 seconds,
25% jitter, 0.5-second flush polling, and batches of 50. A full buffer rejects
the new event explicitly; it never silently drops an accepted important event.

## 17. Phase 8 readiness

Phase 8 may add PostgreSQL, Redis presence/pub-sub, RBAC, credentials, frontend
operational states, and release/security hardening. None are implemented here.
