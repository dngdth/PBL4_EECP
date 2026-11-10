SCREEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS screen_evidence (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES exam_sessions(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    event_id TEXT,
    status TEXT NOT NULL,
    available_until TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_screen_evidence_session
    ON screen_evidence(session_id, agent_id, created_at);
CREATE TABLE IF NOT EXISTS screen_requests (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES exam_sessions(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    payload TEXT NOT NULL,
    response TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deadline TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_screen_requests_session ON screen_requests(session_id, created_at);
CREATE TABLE IF NOT EXISTS screen_frames (
    id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL REFERENCES screen_evidence(id),
    frame_id TEXT NOT NULL,
    metadata TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(evidence_id, frame_id)
);
CREATE INDEX IF NOT EXISTS ix_screen_frames_evidence ON screen_frames(evidence_id, stored_at);
CREATE INDEX IF NOT EXISTS ix_screen_frames_expiry ON screen_frames(expires_at);
"""
