# WebSocket and HTTPS

The production Docker topology has one public entry point:

- Caddy listens on TCP 80/443 and UDP 443, redirects HTTP to HTTPS, and
  terminates TLS.
- `/api/*`, `/health`, `/docs`, and `/openapi.json` are proxied to FastAPI.
- all other paths are served by the Vite SPA through nginx.
- WebSocket upgrades at `/api/v1/ws` are proxied automatically and use `wss`
  when the page is loaded over HTTPS.

Start a localhost deployment with:

```powershell
docker compose up --build
```

Open `https://localhost`. For the LAN control server, configure the address
before starting the stack:

```powershell
$env:EECP_SITE_ADDRESS="https://192.168.3.50"
docker compose up --build
```

For localhost and private IP addresses, Caddy issues certificates from its
local CA. Export that CA certificate:

```powershell
docker compose cp gateway:/data/caddy/pki/authorities/local/root.crt .\eecp-root.crt
```

Install `eecp-root.crt` as a trusted root on teacher browsers. Copy the same
certificate to each managed workstation over a trusted channel and point the
agent at it:

```powershell
$env:EECP_SERVER_URL="https://192.168.3.50"
$env:EECP_CA_CERT_PATH="C:\EECP\eecp-root.crt"
$env:EECP_AGENT_ID="PC01"
python -m agent.main
```

Do not disable TLS verification. Only trust the root certificate exported from
the intended control server. When `EECP_SITE_ADDRESS` is a public DNS name and
ports 80/443 are reachable, Caddy obtains and renews a publicly trusted
certificate automatically.

The WebSocket server publishes lightweight invalidation events after successful
API mutations. The browser then re-fetches the affected state; REST remains the
source of truth. The hub is in-process, so run one API process. A future
multi-process or multi-host API deployment will require shared pub/sub such as
Redis.
