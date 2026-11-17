# Screen evidence: implementation and operation

The user-desktop Agent Client captures compressed, low-resolution context locally.
No browser extension is required. The privileged Service never captures the desktop.
Capture and HTTP upload use separate worker threads, independent of heartbeat.

Screen-capable Clients advertise that capability in Presence. Backend returns
`SCREEN_SESSION_STATE` on heartbeat using its RUNNING-session/policy-ACK truth.
Stopping a session stops new capture and clears the ring/unrequested context within
the next online state sync. Already queued images and their authorized manifests
remain in bounded RAM until Backend ACK or their original retention deadline; they
continue uploading independently of capture, including after another session starts.
They retain their original Agent/session/event identity. This does not stop
privileged enforcement or restore OS settings. During a network
outage the last authorized capture session continues until state can synchronize.
Older Clients do not advertise the capability and do not receive the new message.

## Data flow

1. While an Agent has a verified active policy in a RUNNING exam, capture every 3 seconds, resize to a
   maximum dimension of 960 pixels, encode JPEG quality 40 and retain at most 15
   frames / 30 seconds / 8 MiB of compressed context in RAM.
2. A relevant sensor event allocates an `evidence_id`. Its event payload includes
   that ID before entering the existing durable event spool. Similar events for the
   same target merge within 10 seconds of the original event occurrence time, not
   batch arrival time. Process events require matching PID and process creation
   time to merge; events without that identity stay separate. A group contains at
   most 64 event IDs. The primary event ID remains stable, and the evidence listing
   exposes every linked event ID from persisted telemetry.
3. Pin available context from 30 seconds before through 15 seconds after the event.
   Queue the four closest available frames at/before the event as `event_context`, limited
   to the preceding 30 seconds. Also capture a JPEG at maximum dimension 2560 /
   quality 85 as `trigger` while the event's 15-second post window remains open.
   `captured_at`, `event_occurred_at` and `event_received_at` distinguish historical
   context, event time and delayed Agent receipt. A late event/worker does not take
   a current trigger after that window. Missing history is reported explicitly;
   low-resolution history cannot be reconstructed at higher resolution.
4. Upload at most four `event_context` and one `trigger` image automatically over
   HTTP(S) to `/screen/frames` on the Local Gateway (forwarded upstream to Backend
   `POST /api/v2/screen/ingest/{gateway_id}`). Backend validates the historical
   window/event identity and enforces each representative limit under its storage
   lock, including concurrent ingests. Do not stream the entire ring. Full context/
   replay uploads require a Backend-authorized `SCREEN_REQUEST` delivered over the
   existing Protocol v2 WSS control plane.
5. Gateway verifies the Agent credential against its claimed identity and validates
   size/hash. It atomically persists image + manifest before `gateway_received` ACK.
   Its independent forwarder retries the Backend HTTP(S) ingest route during WAN loss.
6. Backend verifies Gateway identity, Agent binding, exam membership, capture time,
   event linkage or capture manifest, content type/dimensions and SHA-256. Image
   bytes go to content-addressed files; metadata goes to the business database.
   Only after file fsync and DB commit does it return `server_stored=true`.
7. Gateway persists that receipt and deletes its temporary image. A retry after a
   lost ACK returns the receipt instead of creating another evidence record. Client
   retries until a server receipt or its RAM retention limit. Pinned context remains
   available for examiner replay while the session is active and until expiry;
   unrequested context is not sent on reconnect or retained after session stop.

The default RAM evidence pool is 32 MiB and 64 evidence sets, retained for 15 minutes.
Immutable encoded frames are shared by buffers/jobs, so overlapping events do not
copy image bytes. When possible, context is shortened to make room for a representative
image; incomplete context is reported explicitly. All limits are ceilings, not a
promise that a complete window survives memory pressure. Upload is one worker on
Client, up to four independent forwards on Gateway and four ingests on Backend.
Frames are limited to 2 MiB; requests to 32 frames / 16 MiB. Snapshot requests take
one current high-quality image.

## Configuration

| Component / setting | Default |
| --- | --- |
| Client `EECP_SCREEN_ENABLED` | `true` (Gateway mode only) |
| `EECP_SCREEN_INTERVAL_SECONDS` | `3` |
| `EECP_SCREEN_LOW_WIDTH` / `EECP_SCREEN_HIGH_WIDTH` | `960` / `2560` |
| `EECP_SCREEN_PRE_SECONDS` / `EECP_SCREEN_POST_SECONDS` | `30` / `15` |
| `EECP_SCREEN_RING_MAX_FRAMES` / `EECP_SCREEN_RING_MAX_BYTES` | `15` / `8388608` |
| `EECP_SCREEN_RETENTION_SECONDS` | `900` |
| `EECP_SCREEN_EVIDENCE_MAX_BYTES` / `EECP_SCREEN_EVIDENCE_MAX_ITEMS` | `33554432` / `64` |
| Gateway `EECP_GATEWAY_SCREEN_SPOOL_PATH` | `data/gateway-screen` |
| `EECP_GATEWAY_SCREEN_MAX_BYTES` / `EECP_GATEWAY_SCREEN_PER_AGENT_BYTES` | `536870912` / `33554432` |
| `EECP_GATEWAY_SCREEN_TTL_SECONDS` | `86400` |
| Backend `EECP_SCREEN_STORAGE_PATH` | `screens` beside SQLite DB, or explicit persistent volume |
| `EECP_SCREEN_SERVER_RETENTION_DAYS` | `14` |
| `EECP_SCREEN_SERVER_MAX_BYTES` | `10737418240` (10 GiB) |

Tune time, frame count and bytes together when changing the capture interval.
Downsizing limits retained memory, not the original bitmap allocated during capture.
All monitors are captured as one virtual desktop; text may be smaller on a large
multi-monitor setup. Locked/secure desktops return unavailable, and no desktop is
switched or bypassed. Compression/native capture and Python runtime overhead sit
outside the encoded-buffer budgets.

Pending image bytes remain RAM-only on Agent: killing/exiting the Client can lose
images not yet durably received by Gateway. Session stop is different from process
exit and now drains pending uploads without extending capture authority. Durable
event spooling remains independent of image retention. To survive Agent crashes,
a separate protected, quota/TTL-bounded image spool would be needed; this change
does not add one. Existing RAM/item/queue ceilings also apply across draining and
active sessions, so a full queue may reject new evidence instead of growing RAM.

Upgrade Gateway, Backend and Client together for the new `event_context` frame kind.
Older Gateway manifests without optional `event_received_at` still load/retry.

Compose mounts `screen-evidence` for Backend images and stores the Gateway spool
inside the existing persistent `gateway-buffer` volume. The same-host TLS proxy
includes `/api/v2/screen/ingest/` with a 3 MiB body limit. Machine credentials protect
the ingest route independently of examiner authentication. Never expose storage
directories as public static routes. The local file adapter targets one Backend
writer; a multi-replica deployment needs shared storage and coordinated cleanup.

Run `uv sync --all-packages` for native processes. API Docker dependencies include
Pillow for image validation. Run `python -m app.infrastructure.persistence.migrate`
before starting the PostgreSQL API. Migration `0002_screen.sql` adds screen
metadata tables and `0003_users_and_auth.sql` adds multi-user RBAC, token
revocation, and `session_examiners` scoping tables.

## Dashboard and access

The session detail page and workstation inspection modal in the Examiner Dashboard
show stored evidence, capture timestamps, event IDs and current availability via
`/api/v1/sessions/{session_id}/screen/evidence`,
`POST /api/v1/sessions/{session_id}/agents/{agent_id}/screen/requests`,
`GET /api/v1/screen/requests/{request_id}`, and
`GET /api/v1/screen/evidence/{evidence_id}/frames/{frame_id}`. Select an Agent to request
a snapshot, or request the selected evidence's available pre/post frames. The frame
slider displays capture time and trigger/context distinction. Stored images remain
viewable after exam completion; new captures are rejected once the session finishes.
No image is presented as a live video feed. Examiner role and session scope are
checked on list, request, request status and image reads. Reads/requests are audited;
image responses use `private, no-store`. Browser image fetches carry the same bearer
credential as other API calls; tokens are not placed in image URLs.

Client RAM is released when its session ends, changes, Agent closes or retention
expires. Images not durably uploaded before Agent/process/power loss cannot be
recovered. Agent exit is not an instruction to delete Gateway images. The Gateway
keeps unacknowledged images until server ACK or its configured TTL/quota policy;
expiry is logged and retained as a tombstone. The default Gateway metadata ceiling
is 10,000 receipts/manifests. Backend expiry deletes image metadata and unreferenced
files; compact event/evidence/audit metadata remains to explain prior availability.

DNS cache observations are signals to inspect, not proof that a user visited/read a
website. Prefetch, background traffic and blocked navigation can produce events
without the prohibited page being visible. Capture time and event time are distinct.

## Policy hardening

Signed-policy Service mode also wraps enforcement in a protected last-known-good
journal at `%PROGRAMDATA%/EECP/protected-policy.json`. It is HMAC authenticated using
the Service-only verification secret; its directory has SYSTEM/Administrator ACLs.
Per-session maximum policy versions survive Service restart and baseline restore.
A lower version or same-version changed content is rejected. Unexpected changes,
missing state or restoring an older ordinary state file recover the trusted state
and emit security events. OS maintenance continues in the Service with Client off.
Existing Firewall/hosts/USB recovery and durable security journaling remain in use.
Gateway and Backend endpoint addresses are both protected from managed IP rules.

Safe apply rolls back to the prior policy if OS apply or protected-state persistence
fails. It records failed recovery rather than declaring success. Process termination
is irreversible and cannot be undone by policy rollback. The protected journal is
not a hardware monotonic counter and cannot defend against an administrator
restoring the entire protected directory and keys from an old system backup.

For an existing unsigned active installation, first finish/restore through the
authorized Backend and old Service, then upgrade and enable signed mode. An existing
active state without a protected journal is rejected rather than silently trusted.
Keep the verification secret stable; key rotation needs an explicit journal migration.

## Verification

Current automated suite on 2026-11-13 (`uv run pytest apps/api/test -q`, `npm run lint --prefix apps/web`, `npm run build --prefix apps/web`, and `uv run ruff check`) passes with 2 external Postgres/Redis tests skipped (`ss`) when no external DB/Redis is attached. Real multi-monitor/SCM/privileged Windows VM validation remains `PENDING`.

The 2026-11-10 lifecycle/timing fix passed 593 Python tests (2 skipped), four
frontend tests, production TypeScript/Vite build, and Ruff on all changed Python
files. Regression cases include session finish during WAN loss and upload recovery,
capture cancellation, inactive request rejection, late events, pre-event time
bounds, process identity-aware merging, linked event IDs, legacy Gateway manifests,
and concurrent representative-image limits. These are automated adapter tests;
two-machine Windows desktop/SCM validation and new RAM/CPU benchmarks were not run.

Automated tests cover bounded ring/pinned RAM, event merge, post-context, request
limits, unavailable capture, session cleanup, separate ACKs, Gateway restart/quota/
expiry, upload deduplication, manifest/identity/checksum validation, replay, image
retention, scoped reads, persistent anti-rollback, offline state replacement and
safe-apply failure recovery. Existing enforcement/transport regressions also run.

Measure actual Windows capture without saving or uploading images:

```powershell
uv run python -m scripts.windows.diagnose_screen_capture --frames 15 --interval 2
```

The JSON report separates compressed buffer bytes from process working set and
process peak working set. It includes capture latency and CPU time. Results depend
on desktop content and monitor layout. An isolated Windows VM is still required to
exercise real SCM/LocalSystem, multi-monitor changes, lock/unlock and Firewall/hosts/
USB mutation; automated adapter tests do not replace that release validation.

One local Windows run on 2026-11-06 captured 15 frames at a 2-second interval on a
1920x1080 desktop: ring bytes 683,291; high-quality image 311,856 bytes; mean low
capture 64.7 ms; CPU 2.50% of one core over 28.1 seconds. Process working set ended
at 34.4 MB with a 56.5 MB peak, including Python/native capture overhead. This is a
single-desktop measurement, not a capacity guarantee. Raw metrics are recorded in
[`screen-capture-benchmark.json`](screen-capture-benchmark.json).

Validation in this workspace on 2026-11-06: 575 Python tests passed, 2 external-service
tests skipped, including 35 new screen/policy tests. The concurrent evidence test
uses 30 distinct simulated Agents and confirms heartbeat responses during uploads;
its small synthetic images do not constitute a production bandwidth benchmark.
TypeScript/production Vite build, Ruff for all changed Python files, lockfile check
and whitespace check passed. Repository-wide Ruff still reports 16 pre-existing
findings in unchanged files. The browser-control runtime could not initialize, so
Dashboard visual/interactive QA has not been performed. Native capture was measured
on the current single desktop; real multi-monitor/SCM/privileged enforcement tests
remain part of isolated-VM release validation.
