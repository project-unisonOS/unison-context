-- Initial schema for unison-context when using Postgres.
CREATE TABLE IF NOT EXISTS conversation_sessions (
    person_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    messages_json TEXT,
    response_json TEXT,
    summary TEXT,
    updated_at REAL,
    PRIMARY KEY (person_id, session_id)
);

CREATE TABLE IF NOT EXISTS person_profiles (
    person_id TEXT PRIMARY KEY,
    profile_json TEXT,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS dashboard_state (
    person_id TEXT PRIMARY KEY,
    state_json TEXT,
    updated_at REAL
);
