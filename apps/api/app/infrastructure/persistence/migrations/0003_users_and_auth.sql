CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY,
 username TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL,
 full_name TEXT NOT NULL,
 role TEXT NOT NULL CHECK (role IN ('ADMIN', 'EXAMINER')),
 is_active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_users_username ON users(username);

CREATE TABLE IF NOT EXISTS session_examiners (
 session_id TEXT NOT NULL REFERENCES exam_sessions(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 assigned_at TEXT NOT NULL,
 PRIMARY KEY (session_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_session_examiners_user ON session_examiners(user_id);

INSERT INTO schema_migrations(version) VALUES (3) ON CONFLICT DO NOTHING;

