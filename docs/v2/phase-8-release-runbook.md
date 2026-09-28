# Phase 8 Release and Rollback Runbook

## Deploy

1. Provision TLS certificates and populate a protected `.env` from `.env.example`.
2. Back up PostgreSQL and the Gateway `gateway-events.db` volume/file.
3. Start PostgreSQL and Redis; wait for their health checks.
4. Run `python -m app.infrastructure.persistence.migrate` once.
5. Start Backend, Web, then Gateway. Confirm `/health` reports database and Redis online.
6. On each Windows endpoint, configure the per-Agent credential and policy
   verification key using protected machine configuration, then run
   `scripts/windows/install-agent-service.ps1 -Action Install` as Administrator.
7. Start Service before Client and run the release E2E checklist three times.

## Recovery

The Windows Service Control Manager recovery policy restarts the Service after
5, 15, and 60 seconds. Static hosts/Firewall enforcement may survive a Service
stop, but maintenance stops. Administrator anti-tamper is not claimed.

## Rollback

1. Stop new session/control operations and preserve the Gateway SQLite buffer.
2. Restore active sessions through the normal `RESTORE_BASELINE` command before
   downgrading Agent components. If unavailable, use the documented Service
   restore procedure rather than resetting all Windows Firewall rules.
3. Roll back Web, Gateway, Client, Service, and Backend artifacts to a mutually
   compatible Protocol v2 release.
4. Database rollback is supported only when the older release accepts the current
   schema. Migration `0001` has no destructive automatic downgrade; restore the
   pre-deploy PostgreSQL backup when a schema downgrade is unavoidable.
5. Revoke compromised machine credentials and deploy replacements. Never restore
   a known-compromised secret from backup.
6. Keep `gateway-events.db`; the prior compatible Gateway can resume pending events.

## E2E checklist

Verify PostgreSQL, Redis, Backend, authenticated Gateway/Agent/Examiner, Service,
signed policy deployment, hosts/Firewall enforcement, applied-hash ACK, detection,
WAN buffering/recovery, dedupe, finish/restore, and audit-chain verification.
Run `scripts/release-e2e.ps1` for the CI-safe vertical scenarios. Elevated Windows
checks require an isolated VM and must use cleanup in `finally`.

## Signing

Executable/installer signing is not configured because no organization signing
certificate is available. Do not label artifacts as signed until the release
pipeline owns and verifies an appropriate certificate.
