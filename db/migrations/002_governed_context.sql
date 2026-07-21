-- Phase 2 governed context is created idempotently by GovernedContextRepository.
-- This marker is retained for migration inventory and external database tooling.
CREATE TABLE IF NOT EXISTS unison_schema_versions (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);

INSERT INTO unison_schema_versions (component, version, applied_at)
VALUES ('governed_context', 2, CURRENT_TIMESTAMP)
ON CONFLICT (component) DO UPDATE SET version=excluded.version, applied_at=excluded.applied_at;
