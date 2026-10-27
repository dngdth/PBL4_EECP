# Two-machine Enforce demo runbook

This runbook targets the reviewed `feat/khoa` build. It does not claim that a real
Windows E2E has passed. Replace every angle-bracket placeholder locally and never
commit populated secrets, private keys, tokens, or `.env` files.

## A. Machine A — Server

Machine A hosts PostgreSQL, Redis, Central Backend, Dashboard and Local Gateway.
Use a certificate whose SAN contains the Machine A DNS name/IP used by Machine B,
and also `gateway-backend-tls` for the private same-host Backend uplink.

1. Copy `.env.example` to `.env`. Set strong unique values and matching credential
   hashes for `GW-A` and `AGT-001`. Set certificate/CA paths, but do not paste secret
   values into this runbook.
2. Start the same-server stack from the repository root:

   ```powershell
   docker compose -f compose.yaml -f compose.gateway-server.yaml --profile gateway up --build -d
   docker compose -f compose.yaml -f compose.gateway-server.yaml --profile gateway ps
   ```

3. Permit inbound TCP 8443 from Machine B's LAN address to the Gateway. Permit
   TCP 3000/8000 only from examiner/admin networks as needed. Do not expose
   PostgreSQL or Redis to the LAN.
4. Verify and inspect logs:

   ```powershell
   Invoke-RestMethod http://localhost:8000/health
   Invoke-RestMethod https://<SERVER_LAN_DNS_OR_IP>:8443/health
   docker compose -f compose.yaml -f compose.gateway-server.yaml --profile gateway logs --tail 100 api local-gateway web
   ```

   Use a CA trusted by the host. Do not use certificate-skip flags as demo proof.
   Open `http://<SERVER_LAN_IP>:3000` only if the lab web endpoint intentionally
   uses HTTP; use the site's HTTPS reverse proxy for a production-like demo.

5. Stop without deleting volumes:

   ```powershell
   docker compose -f compose.yaml -f compose.gateway-server.yaml --profile gateway stop
   ```

Required Backend variables are `DATABASE_URL`, `REDIS_URL`,
`EECP_ENVIRONMENT=production-like`, `EECP_AUTH_SIGNING_KEY`,
`EECP_EXAMINER_USERNAME`, `EECP_EXAMINER_PASSWORD_HASH`,
`EECP_EXAMINER_ROLE`, `EECP_GATEWAY_CREDENTIALS_JSON`,
`EECP_GATEWAY_BOOTSTRAP_TOKEN`, `EECP_POLICY_SIGNING_KEY`, and
`EECP_COMMAND_SIGNING_KEY`.

Required Gateway variables are `EECP_GATEWAY_ID`, `EECP_GATEWAY_ROOM_ID`,
`EECP_GATEWAY_BACKEND_WS_URL`, `EECP_GATEWAY_BACKEND_CA_FILE`,
`EECP_GATEWAY_BOOTSTRAP_TOKEN`, `EECP_AGENT_GATEWAY_TOKEN`,
`EECP_AGENT_CREDENTIALS_JSON`, `EECP_GATEWAY_TLS_CERTFILE`, and
`EECP_GATEWAY_TLS_KEYFILE`. The Agent-facing listener is TCP 8443 by default.
Production-like mode requires WSS and rejects plaintext WS.

## B. Machine B — Student workstation

Use 64-bit Python and the checked-out repository. In an ordinary PowerShell:

```powershell
uv sync --all-packages
uv pip install --python .\.venv\Scripts\python.exe pywin32
```

Create a temporary `service.env` outside the repository. It is parsed as literal
`NAME=VALUE` lines, not as PowerShell. A minimal production-like Enforce file is:

```text
EECP_AGENT_ID=AGT-001
EECP_ENVIRONMENT=production-like
EECP_POLICY_MODE=enforce
EECP_GATEWAY_URL=wss://<SERVER_LAN_DNS_OR_IP>:8443/ws/agents
EECP_POLICY_VERIFICATION_KEY=<MATCH_BACKEND_POLICY_SIGNING_KEY>
EECP_COMMAND_VERIFICATION_KEY=<MATCH_BACKEND_COMMAND_SIGNING_KEY>
EECP_REQUIRE_SIGNED_POLICY=true
EECP_REQUIRE_AUTHORIZED_COMMANDS=true
EECP_SERVICE_DATA_ROOT=C:\ProgramData\EECP
EECP_POLICY_STATE_PATH=C:\ProgramData\EECP\policy-state.json
EECP_COMMAND_REPLAY_PATH=C:\ProgramData\EECP\command-replay.json
```

The Service reads the protected installed copy at
`C:\ProgramData\EECP\service.env` before importing Agent configuration. It does
not rely on an Administrator PowerShell process environment being inherited by
SCM. Only an allowlist of Service settings is accepted.

In an elevated PowerShell, install the local event token and Service:

```powershell
.\scripts\windows\setup-security-event-ipc.ps1 -Action Install -AgentUser <STANDARD_USER>
.\scripts\windows\install-agent-service.ps1 -Action Install -PythonExe ".\.venv\Scripts\python.exe" -EnvironmentFile "<PATH_TO_TEMP_SERVICE_ENV>"
.\scripts\windows\install-agent-service.ps1 -Action Start -PythonExe ".\.venv\Scripts\python.exe"
```

The installer restricts `C:\ProgramData\EECP` to LocalSystem and Administrators,
checks pywin32, and rejects an SCM identity other than `LocalSystem`. Delete the
temporary source `service.env` securely after checking the installed ACL. Never
give the Standard User either verification key.

In the Standard User's PowerShell, configure only Client values and start it:

```powershell
$env:EECP_AGENT_ID = 'AGT-001'
$env:EECP_ENVIRONMENT = 'production-like'
$env:EECP_POLICY_MODE = 'enforce'
$env:EECP_GATEWAY_URL = 'wss://<SERVER_LAN_DNS_OR_IP>:8443/ws/agents'
$env:EECP_AGENT_GATEWAY_TOKEN = '<AGT-001_GATEWAY_CREDENTIAL>'
$env:EECP_GATEWAY_CA_FILE = 'C:\ProgramData\EECP-Client\gateway-ca.crt'
.\.venv\Scripts\python.exe -m agent.client.main --check
.\.venv\Scripts\python.exe -m agent.client.main
```

Do not run the Client as Administrator. Its Gateway credential must match the
`AGT-001` hash in `EECP_AGENT_CREDENTIALS_JSON`. The local event token is read
from `C:\ProgramData\EECP-Client\local-event.token`; its ACL authorizes only
LocalSystem, Administrators, and the selected Standard User. Policy and command
verification remains inside the privileged Service.

Service controls and logs:

```powershell
.\scripts\windows\install-agent-service.ps1 -Action Status
.\scripts\windows\install-agent-service.ps1 -Action Restart -PythonExe ".\.venv\Scripts\python.exe"
Get-WinEvent -LogName Application -MaxEvents 100 | Where-Object ProviderName -Match 'Python|EECP'
```

Stop the Client with Ctrl+C. Stop/remove the Service only from elevated
PowerShell:

```powershell
.\scripts\windows\install-agent-service.ps1 -Action Stop -PythonExe ".\.venv\Scripts\python.exe"
.\scripts\windows\install-agent-service.ps1 -Action Remove -PythonExe ".\.venv\Scripts\python.exe"
```

## C. LAN and read-only preflight

Discover addresses rather than copying a sample address:

```powershell
Get-NetIPConfiguration | Select-Object InterfaceAlias,IPv4Address,IPv4DefaultGateway
Test-NetConnection <SERVER_LAN_DNS_OR_IP> -Port 8443
Test-NetConnection <SERVER_LAN_DNS_OR_IP> -Port 8000
```

On Machine B, validate the certificate and WSS prerequisite with the same trusted
CA used by the Agent. A successful HTTPS Gateway health request validates DNS/IP,
TCP, TLS chain and SAN before the WebSocket authentication test:

```powershell
Invoke-RestMethod https://<SERVER_LAN_DNS_OR_IP>:8443/health
Get-Service EECPAgentService
Get-CimInstance Win32_Service -Filter "Name='EECPAgentService'" | Select-Object Name,State,StartName,PathName
Get-ChildItem \\.\pipe\ | Where-Object Name -EQ 'eecp-agent-v2'
icacls.exe C:\ProgramData\EECP
icacls.exe C:\ProgramData\EECP-Client\local-event.token
```

The same non-mutating checks are packaged as:

```powershell
.\scripts\windows\validate-enforce-readonly.ps1 -ServerHost <SERVER_LAN_DNS_OR_IP>
```

On Machine A:

```powershell
Get-NetTCPConnection -State Listen | Where-Object LocalPort -In 8000,8443
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod https://localhost:8443/health
```

Confirm Agent authentication and `ONLINE` state in Dashboard → Workstations.
`ONLINE` plus a healthy Service/policy hash is required; TCP reachability alone is
not authentication. For Firewall diagnosis, use this read-only identity check:

```powershell
Get-NetFirewallRule -Name 'EECP-*' | Select-Object Name,DisplayName,Group,Enabled,Direction,Action,Profile
Get-NetFirewallRule -Name 'EECP-*' | Get-NetFirewallAddressFilter | Select-Object InstanceID,RemoteAddress
```

## D. Final demo validation checklist

Record each case as `PASS`, `FAIL`, or `BLOCKED`, with timestamps from both hosts.

### T01 — Machine B online

- Prerequisites: Service Running as LocalSystem; Client is Standard User; 8443 and
  certificate checks pass.
- Steps: start Client, sign in to Dashboard, open Workstations.
- Expected: `AGT-001` becomes ONLINE with the expected Gateway and no auth errors.
- Evidence: Dashboard screenshot, Client connection log, Gateway log, `/health`.
- If failed: compare Agent ID/credential hash, certificate SAN/CA, system clocks,
  Gateway agent handshake log and inbound firewall.
- Cleanup: leave components running for T02.

### T02 — policy apply and integrity

- Prerequisites: T01 PASS; signed policy and command keys match.
- Steps: create/start an Enforce session containing `AGT-001`; wait for APPLY ACK.
- Expected: session RUNNING, policy status APPLIED, Service health healthy and
  active policy hashes agree.
- Evidence: Dashboard ACK/hash, Backend audit, Service Application log and the
  policy state file metadata (do not publish secrets).
- If failed: inspect Backend command signature, Client ACK, Named Pipe availability,
  Service verification key and policy hash.
- Cleanup: keep session running.

### T03 — harmless denied executable

- Prerequisites: a team-owned, harmless, restartable test executable is in the
  denylist. Never use `cmd.exe`, `powershell.exe`, security tools, or system processes.
- Steps: launch one instance as Standard User and record its PID.
- Expected: Service validates PID plus creation time, terminates it, and Dashboard
  shows FORBIDDEN_PROCESS/`BLOCKED` with that PID exactly once.
- Evidence: PID before launch, Service log, Security Event event_id, Gateway and
  Backend receipt, Dashboard row.
- If failed: inspect process owner/CIM query, `QUERY_FAILED` health degradation,
  taskkill result, journal, Agent spool and Gateway buffer.
- Cleanup: remove the test executable from policy after the session.

### T04 — blocked domain after policy apply

- Prerequisites: T02 PASS; capture the target domain's A/AAAA answers before Apply.
- Steps: after Apply, make a new browser request and a new TCP connection to the
  blocked domain. Test IPv4 and IPv6 when available.
- Expected: classify the result as `DOMAIN_BLOCKED`, `DOMAIN_STILL_ACCESSIBLE`, or
  `INCONCLUSIVE`. A page failing to render alone is not packet-level proof.
- Evidence: DNS snapshot, exact inbound/outbound EECP rules, browser Network trace,
  timestamps and packet capture from the isolated lab when permitted.
- If failed: inspect DoH/VPN/proxy use, shared CDN addresses, Gateway overlap,
  Firewall profiles, Service maintenance error and rule verification fields.
- Cleanup: keep the session running for T05.

### T05 — connection/tab opened before policy apply

- Prerequisites: restore baseline first; open a controlled TCP/UDP stream and the
  target browser tab before starting a new Enforce session.
- Steps: exchange baseline data, Apply policy, then send new data over the same
  sockets. Repeat with a new connection; test UDP/QUIC and IPv6 when feasible.
- Expected: record each transport separately as `DOMAIN_BLOCKED`,
  `DOMAIN_STILL_ACCESSIBLE`, or `INCONCLUSIVE`. Already-rendered/buffered page data
  is not proof that new network data passed.
- Evidence: peer logs, packet timestamps, browser Network trace, resolved IP set,
  Firewall rules and Gateway connectivity throughout the test.
- If failed: verify the active connection's remote IP was discovered before hosts
  mutation; check browser-private DNS, proxy/VPN and Windows Firewall behavior.
- Cleanup: finish and restore this test session before continuing.

Session-monitoring regression should also be sampled here: LOCK then UNLOCK must
produce distinct USER_SESSION_CHANGED events without a startup fake LOGON.

### T06 — managed integrity recovery

- Prerequisites: isolated lab machine and saved baseline; T02 PASS.
- Steps: elevated tester changes only one EECP-owned Firewall rule or the text
  between EECP markers in hosts. Never alter school/user-owned rules.
- Expected: CONFIG_CHANGED then RECOVERED; exact Name, DisplayName, Group,
  direction/action/profile and remote address are restored. The next maintenance
  pass creates no false CONFIG_CHANGED.
- Evidence: before/after read-only Firewall output, hosts diff limited to EECP
  markers, paired event IDs and Service health.
- If failed: inspect `_rule_matches` fields, PowerShell error, Service log and ACL.
- Cleanup: allow automatic recovery, then compare the complete OS baseline.

### T07 — offline delivery and pending-event synchronization

- Prerequisites: T02 PASS; Gateway/Backend remain running.
- Steps: stop only the Standard User Client; trigger the harmless denied executable;
  verify Service still enforces; finish the session on Machine A; restart Client.
- Expected: the Service journal retains the original event_id/occurred_at. Client
  discovers pending session metadata, writes to Agent spool before journal ACK,
  submits it as a late event within 24 hours, and does not reactivate Enforce.
  Duplicate delivery is idempotent and the old event does not reactivate Enforce.
- Evidence: Service journal count, Agent/Gateway logs, stable event_id across hops,
  Backend late-arrival audit, Dashboard event and final OS baseline.
- If failed: inspect token/Agent binding, journal, Agent spool quarantine/backoff,
  Gateway `FAILED_TERMINAL`, Backend rejection reason and session timestamps.
- Cleanup: preserve evidence and continue to T08.

### T08 — finish, restore and network baseline

- Prerequisites: T04–T07 evidence captured; original Firewall/hosts/USB baseline saved.
- Steps: Finish Session, execute Restore, open an allowed site and repeat baseline
  TCP/UDP checks. Query only exact EECP rule names.
- Expected: recorded EECP-owned rules and hosts block are gone, USB equals baseline,
  allowed networking and Gateway connectivity work, and no unrelated rule changed.
- Evidence: before/after OS state, RESTORE ACK, Service/Gateway logs and allowed-site
  request trace.
- If failed: do not reset Windows Firewall globally; inspect persisted policy state,
  exact managed rule list, restore ACK and Service errors.
- Cleanup: stop Client/Service only after evidence collection.

## E. Cleanup and acceptance boundary

Before leaving the lab, confirm no EECP-owned Firewall rule or managed hosts block
remains, USB state equals its captured baseline, the test process is absent, and
the session is no longer RUNNING. Preserve databases/logs when investigating a
failure. Do not delete evidence or reset Windows Firewall globally.

The build is only `TWO-MACHINE DEMO VERIFIED` after T01–T08 pass on the two real
Windows machines. Mock/unit tests and this runbook establish code readiness, not
that deployment result.
