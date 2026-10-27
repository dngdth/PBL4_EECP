# Domain traffic enforcement

Domain policies now install Windows Firewall blocks as well as the existing
managed hosts entries. No browser extension, UI inspection, TLS decryption, or
browser restart is involved. Previously displayed/cached content is unaffected.
The rule applies to traffic, regardless of whether it is a question, refresh,
background fetch, WebSocket, or streamed response.

## Apply and maintenance

1. The privileged Service snapshots A/AAAA entries in the Windows DNS cache,
   including cached subdomains of each blocked category/domain, and queries the
   domain and its `www` alias with `Resolve-DnsName -NoHostsFile`.
2. Real unicast addresses are collected before hosts mutation and `flushdns`.
   Sinkholes (`127.0.0.1`, `::1`, unspecified addresses), multicast and link-local
   addresses are excluded. Every required domain needs at least one known real
   address; otherwise apply fails before OS mutation rather than reporting an
   incomplete domain policy as applied.
3. All addresses pass the existing protected-Gateway overlap validation. The
   Service installs one grouped outbound block and one grouped inbound block,
   with `protocol=any` and `profile=any`. Both IPv4 and IPv6 are included. These
   rules target the remote addresses for all programs, ports and transports,
   including TCP/HTTPS and UDP/QUIC. New rules precede removal of stale rules.
4. Hosts/process/USB controls are applied as before. State is saved atomically
   only after all apply steps succeed. Derived addresses do not change the
   policy contract, version, hash or signature. Failures roll back managed rules
   and hosts to the previous state.
5. Service maintenance repairs missing rules every maintenance cycle and
   refreshes DNS at most once per 60 seconds. Old addresses are retained while
   the domain remains blocked, because existing connections can still use them.
   Refresh failures leave the old state/rules intact and surface as maintenance
   errors. Restart reconstructs both directions from the persisted state. A
   policy update removes addresses belonging only to removed domains; restore
   deletes only the exact recorded EECP-owned rules and restores hosts/USB.

Commands and maintenance are serialized inside the enforcer. DNS discovery is
bounded at eight seconds and individual Firewall commands at five seconds. The
Client IPC request timeout is 30 seconds to accommodate DNS and OS reconciliation.
Audit mode still records intent only; it does not resolve domains or install rules.

## Established connections and limits

Windows Filtering Platform supports policy-change reauthorization of existing
ALE flows on subsequent packets. The inbound block also covers traffic coming
back on an existing response/stream, not merely future DNS lookups. However,
installed-rule verification is not a packet-level acceptance test. Run the
live smoke test below on each supported Windows environment before claiming
that existing TCP/QUIC traffic is stopped there.

This is destination-IP enforcement, not URL inspection:

- DNS cache and answers may not contain every address a browser already knows,
  especially with browser-private encrypted DNS, a VPN/proxy, or expired cache
  entries. A pre-existing connection to an undiscovered address cannot be
  reliably attributed to a domain by this adapter. Explicit policy IPs/CIDRs
  remain available. Do not claim universal anti-bypass coverage.
- Services sharing a blocked IP can be affected. Retaining previous addresses
  until policy removal favors stopping persistent connections over minimizing
  collateral blocks during the exam.
- Only cached subdomains plus the explicit domains/`www` aliases are discovered;
  arbitrary unknown subdomains are not enumerated. Include additional API
  domains in the policy when needed.
- Bytes already delivered to the browser cannot be retracted. A page may remain
  visible, show locally generated UI, or briefly display buffered response data.
- The adapter does not interpret questions or create a new packet-level violation
  event. Existing loopback monitoring continues for hosts-redirected attempts.
- Rule verification currently parses English `netsh` output. Disabled firewall
  profiles, third-party filtering and system policy restrictions must be checked
  during deployment; a rule existing alone is not proof of packet enforcement.

References: [ALE reauthorization](https://learn.microsoft.com/en-us/windows/win32/fwp/ale-re-authorization),
[Resolve-DnsName](https://learn.microsoft.com/en-us/powershell/module/dnsclient/resolve-dnsname).

## Automated tests

From the repository root:

```powershell
uv run pytest apps/api/test/unit/test_domain_traffic.py apps/api/test/unit/test_phase6_firewall.py apps/api/test/unit/test_agent_policy.py apps/api/test/integration/test_phase6_full_vertical.py
```

These tests use fake Firewall/DNS adapters. Windows-specific tests execute the
real PowerShell parsing code with fake DNS cmdlets, without DNS or OS mutation.

## Live existing-connection test

Validation on 2026-10-27: the full automated suite passed (`447 passed`,
`2 skipped`; external PostgreSQL/Redis services were not configured), lint and
diff checks passed, and real DNS queries returned IPv4/IPv6 destinations for
all six generative-AI category domains. The live packet-blocking smoke test
was **not run**: this execution session is not a Windows Administrator. Its
preflight correctly refuses to mutate Firewall in that session. Automated
adapter tests are not a substitute for this outstanding Windows validation.

Use two test machines. The peer must not be the Gateway or a production server.
Allow the echo port for TCP and UDP on the peer through its normal lab setup.

On the second machine, from this repository:

```powershell
python -m scripts.windows.verify_domain_traffic --serve --bind 0.0.0.0
```

On the Windows client, open an Administrator terminal in this repository:

```powershell
python -m scripts.windows.verify_domain_traffic --peer 192.168.1.50
```

Replace the address with the echo peer's IP. For IPv6, start the peer with
`--bind ::` and pass its IPv6 address to the client. The client checks that
Firewall profiles are enabled, opens TCP and UDP sockets and exchanges data,
applies a synthetic domain policy, retries data on those **same sockets**, checks
new TCP traffic, restores policy, then checks TCP/UDP again. It uses a temporary
hosts file and removes exact test rules in `finally`, including after failure.
A failed baseline or a failed blocked-traffic check is a failure, not a pass.

Finally validate the real browser: open ChatGPT before policy, verify a baseline
response, apply the actual generative-AI policy, and attempt another request.
Also apply mid-response, test QUIC and IPv6, and verify Gateway connectivity and
recovery after restore. The echo smoke test does not prove complete domain/IP
discovery or every browser's QUIC implementation.
