# unison-context

Context, profile, dashboard, and conversation store for UnisonOS.

## Status
Core service (active). The current implementation is a FastAPI service in `src/server.py` with local SQLite-backed profile and conversation storage plus best-effort key/value passthrough to `unison-storage`.

## What is implemented
- Conversation history endpoints for per-person, per-session companion state.
- Profile read/write endpoints with optional Fernet encryption via `UNISON_CONTEXT_PROFILE_KEY`.
- Dashboard read/write endpoints for persisted cards and layout preferences.
- Key/value helpers backed by `unison-storage`.
- Health, readiness, and Prometheus-style metrics endpoints.
- Optional policy-group validation and consent enforcement controlled from `src/settings.py`.

## API surface
- `GET /health`, `GET /healthz`
- `GET /ready`, `GET /readyz`
- `GET /metrics`
- `GET /conversation/health`
- `POST /conversation/{person_id}/{session_id}`
- `GET /conversation/{person_id}/{session_id}`
- `GET /profile/{person_id}`
- `POST /profile/{person_id}`
- `POST /profile.export`
- `GET /dashboard/{person_id}`
- `POST /dashboard/{person_id}`
- `POST /kv/put`
- `POST /kv/set`
- `POST /kv/get`

## Run locally
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -c ../constraints.txt -r requirements.txt
cp .env.example .env
python src/server.py
```

## Key configuration
- `UNISON_STORAGE_HOST`, `UNISON_STORAGE_PORT`
- `UNISON_POLICY_HOST`, `UNISON_POLICY_PORT`
- `UNISON_POLICY_VALIDATE_GROUPS`
- `UNISON_REQUIRE_CONSENT`
- `UNISON_CONTEXT_DB_PATH`
- `UNISON_CONTEXT_DATABASE_URL`
- `UNISON_CONTEXT_PROFILE_KEY`

## Tests
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -c ../constraints.txt -r requirements.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OTEL_SDK_DISABLED=true python -m pytest tests
```

## Docs
- Public docs: https://project-unisonos.github.io
- Repo docs: `SETUP.md`, `SECURITY.md`
