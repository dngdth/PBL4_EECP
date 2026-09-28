CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO schema_migrations(version) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS exam_sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, hostname TEXT NOT NULL, ip_address TEXT NOT NULL, status TEXT NOT NULL, agent_version TEXT NOT NULL, last_seen TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_agents_status_last_seen ON agents(status, last_seen);
CREATE TABLE IF NOT EXISTS gateways (id TEXT PRIMARY KEY, room_id TEXT NOT NULL, status TEXT NOT NULL, version TEXT NOT NULL, last_seen TEXT NOT NULL, created_at TEXT NOT NULL, connected_agent_count INTEGER NOT NULL DEFAULT 0, backend_uplink_status TEXT NOT NULL DEFAULT 'OFFLINE');
CREATE INDEX IF NOT EXISTS ix_gateways_status_last_seen ON gateways(status, last_seen);
CREATE TABLE IF NOT EXISTS agent_gateway_bindings (agent_id TEXT PRIMARY KEY REFERENCES agents(id), gateway_id TEXT NOT NULL REFERENCES gateways(id), bound_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_agent_gateway_bindings_gateway ON agent_gateway_bindings(gateway_id, agent_id);
CREATE TABLE IF NOT EXISTS session_workstations (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES exam_sessions(id), agent_id TEXT NOT NULL REFERENCES agents(id), assigned_at TEXT NOT NULL, UNIQUE(session_id, agent_id));
CREATE INDEX IF NOT EXISTS ix_session_workstations_session ON session_workstations(session_id, assigned_at, id);
CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES exam_sessions(id), target_id TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, acknowledged_at TEXT, error TEXT, attempt_count INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT, next_retry_at TEXT, expires_at TEXT);
CREATE INDEX IF NOT EXISTS ix_commands_target_status ON commands(target_id, status);
CREATE INDEX IF NOT EXISTS ix_commands_delivery ON commands(target_id, status, next_retry_at);
CREATE TABLE IF NOT EXISTS policy_profiles (id TEXT PRIMARY KEY, label TEXT NOT NULL, description TEXT NOT NULL, rules TEXT NOT NULL, is_builtin INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS telemetry_events (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES exam_sessions(id), workstation_id TEXT NOT NULL, event_type TEXT NOT NULL, severity TEXT NOT NULL, category TEXT NOT NULL, action TEXT NOT NULL, destination TEXT, correlation_id TEXT, payload TEXT NOT NULL, occurred_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_telemetry_session ON telemetry_events(session_id, occurred_at);
CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES exam_sessions(id), workstation_id TEXT, category TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_incidents_session ON incidents(session_id, created_at);
CREATE TABLE IF NOT EXISTS audit_events (sequence BIGSERIAL PRIMARY KEY, id TEXT UNIQUE NOT NULL, session_id TEXT REFERENCES exam_sessions(id), actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, details TEXT NOT NULL, previous_hash TEXT NOT NULL, chain_hash TEXT NOT NULL, occurred_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_audit_session ON audit_events(session_id, sequence);
