# Phase 5 — Local Gateway

## 1. Goal

Phase 5 inserts a standalone Local Gateway into the Agent control plane while keeping
the Phase 4 privilege boundary unchanged. Central Backend remains the authority for
sessions, policies, commands, acknowledgements, audit, and persistent business state.

## 2. Before

```text
Agent Client -> HTTP register/heartbeat/poll/ACK/event -> Central Backend
Agent Client -> Named Pipe -> Privileged Agent Service
```

## 3. After

```text
Agent Client <-> WSS <-> Local Gateway <-> WSS <-> Central Backend
Agent Client  -> Named Pipe -> Privileged Agent Service
```

The Local Gateway is an application, not the reverse proxy/TLS concept sometimes also
called a gateway. This repository had no Caddy/Nginx service before Phase 5. Compose
uses the unambiguous service name `local-gateway`.
The Compose service is behind the `gateway` profile so existing API/Web startup is not
changed until deployment supplies Gateway IDs and bootstrap tokens.

## 4. Gateway responsibilities

- Accept authenticated Agent WebSocket connections.
- Bind a validated logical Agent identity to each socket.
- Keep an in-memory Agent connection registry and local presence.
- Route Backend commands only to their target Agent.
- Forward Agent ACK, Event, and Presence messages without changing their payload.
- Maintain a persistent Backend uplink with exponential backoff and jitter.
- Report Gateway health and re-announce connected Agents after reconnect.

## 5. Gateway non-responsibilities

The Gateway cannot create or edit policies, authorize sessions, classify cheating,
access Named Pipes, control Windows, execute privileged commands, or become a business
database. It has no policy engine, SQLite repository, Redis, shell execution, or
arbitrary RPC surface.

## 6. Gateway identity

`EECP_GATEWAY_ID` and `EECP_GATEWAY_ROOM_ID` are required configuration. Gateway
version, listener, TLS, Backend URL, reconnect, health, and presence settings also come
from environment variables. No room, Gateway identity, Backend IP, or credential is
hard-coded in application source.

Backend persists a `Gateway` entity with ID, room, status, version, `last_seen`, and
`created_at`. Raw WebSocket objects stay only in an ephemeral connection registry.

## 7. Agent → Gateway connection

The production Agent uses `GatewayControlClient` and a persistent WSS connection. Its
first message is a strict Protocol v2 `AgentHello` in `GatewayEnvelope`. The Gateway
validates the shared bootstrap token, schema, protocol version, and logical identity.
It never derives identity from IP.

Identity remains bound to the connection. An ACK, Event, or Presence claiming another
`source_id` is rejected and the socket is closed. When the same Agent reconnects, the
newest connection wins and the stale socket is closed.

Plaintext `ws://` requires `EECP_GATEWAY_ALLOW_PLAINTEXT_WS=true` and is development
only. Production startup requires an Agent-listener TLS certificate/key and uses WSS;
there is no silent downgrade.

Direct Backend HTTP remains available only through the explicit Agent flag
`--direct-backend-compat`. It is not the production default.

## 8. Gateway → Backend uplink

The Gateway opens one logical persistent WSS uplink at:

```text
/api/v2/gateways/ws/{gateway_id}
```

It sends `GatewayHello`, periodic `GatewayHealth`, Agent hello/presence, ACK, Event, and
delivery failure messages. Backend sends `Command` messages through the same channel.
Reconnect uses configurable capped exponential backoff with jitter. On reconnect the
Gateway re-announces currently connected Agents so Backend mappings can be rebuilt.

## 9. Agent-Gateway mapping

SQLite tables `gateways` and `agent_gateway_bindings` persist registry and mapping.
`agent_id` is the binding primary key, enforcing one active logical Gateway mapping per
Agent. Rebinding atomically replaces the old mapping. IP remains metadata only.

Backend use cases register/update Gateways, bind/rebind Agents, resolve an Agent's
Gateway, and list Agents for a Gateway. HTTP read endpoints expose Gateway health and
mapping for operational inspection; writes originate from the authenticated uplink.

## 10. Routing

### Command

Backend resolves bindings, loads pending commands using existing retry/expiry
semantics, converts them to the existing strict Protocol v2 `Command`, and sends the
original validated payload to the owning Gateway. Gateway performs an exact target
lookup and never broadcasts. If the Agent is offline, Gateway emits a delivery failure;
it never marks execution successful.

### ACK

Agent sends the existing `Ack`. Gateway validates connection identity and forwards the
same command ID, correlation ID, status, and applied hash. Backend calls the existing
acknowledgement use case, which remains the persistence/audit authority.

### Event

Agent sends the existing `Event`. Gateway validates the embedded Agent identity and
forwards it. Backend performs existing session membership, state, incident, and audit
logic. Gateway does not create incidents.

### Presence

Gateway stores ephemeral `Presence` in memory and reports ONLINE/OFFLINE/DEGRADED to
Backend. A disconnect is health state, not evidence of cheating. Missing heartbeat on
an otherwise open socket becomes DEGRADED after the configured timeout.

## 11. Reconnect

Both Agent→Gateway and Gateway→Backend use capped exponential backoff with jitter and
avoid tight retry loops. Agent LAN sockets are not intentionally dropped during a
Backend restart. Gateway reconnect re-announces connected identities and presence.
Agent reconnect replaces its stale socket and preserves a single active connection.

## 12. Presence

Gateway presence and connection state are in memory only. Backend Gateway and
Agent→Gateway mapping are persistent SQLite state. Gateway restart clears local
presence; reconnecting Agents rebuild it. Backend liveness remains based on backend
timestamps rather than frontend time.

Gateway health includes gateway ID, room ID, status, last seen, connected Agent count,
and Backend uplink status.

## 13. Authentication status

Phase 5 uses two environment-provided shared bootstrap tokens:

- `EECP_AGENT_GATEWAY_TOKEN` for Agent→Gateway.
- `EECP_GATEWAY_BOOTSTRAP_TOKEN` for Gateway→Backend.

Tokens are required, compared before accepting the relevant identity, never hard-coded,
and never logged by the new code. This is provisional identity validation, not
production-grade Agent enrollment, per-device credentials, mTLS, rotation, or PKI.
Phase 8 must replace it with proper credentials and authorization.

## 14. Failure behavior

- Offline Agent: explicit delivery failure; no silent drop or false success.
- Backend restart: Gateway reconnects; connected Agent LAN sockets remain available.
- Gateway restart: Agents reconnect and rebuild local presence/mapping announcements.
- Agent restart: newest connection replaces stale connection.
- Malformed or spoofed Agent message: rejected without crashing the Gateway process.
- Malformed Gateway uplink message: structured error where possible; connection stays
  available for subsequent valid messages.

## 15. Known limitations

- No durable Gateway offline buffer exists. In-memory queued ACK/events can be lost if
  the Gateway process stops, and an in-flight item can be lost during WAN failure.
- No delivery exactly-once guarantee is claimed; Backend command retry remains the
  recovery mechanism.
- Local presence is rebuilt after Gateway restart.
- No Redis, PostgreSQL, RabbitMQ/Kafka, HA, or multi-instance coordination exists.
- Shared bootstrap tokens are not a full credential/enrollment system.
- Production certificates are deployment inputs; tests use explicit plaintext dev mode.

## 16. Tests

Tests cover Gateway persistence and health, Agent mapping/rebinding, multiple Agent
connections, exact-target command routing, offline delivery failure, ACK/Event/Presence
forwarding, correlation preservation, duplicate connections, disconnect/reconnect,
Backend uplink reconnect, periodic health, malformed messages, identity spoofing,
production Agent composition, architecture boundaries, and the unchanged real Windows
Named Pipe regression.

## 17. Phase 6 readiness

Phase 6 can exercise the full vertical path:

```text
Backend Command
  -> Gateway
  -> Agent Client
  -> Named Pipe
  -> Agent Service
  -> ServiceResult
  -> Agent Client ACK
  -> Gateway
  -> Backend
```

It may then add the explicitly deferred enforcement mapping:

```text
Domain -> hosts
IP/CIDR -> Windows Firewall
```

Phase 5 does not implement Windows Firewall, WFP, blocked IP/CIDR rules, durable event
buffering, PostgreSQL, Redis, full RBAC, or Phase 8 credentials.
