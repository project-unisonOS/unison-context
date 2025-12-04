# unison-context

## Status
Core service (active) â€” context/profile store used by orchestrator and renderer; exposed on `8081` in devstack.

### Profiles and secure storage
- Person profiles are stored in a local SQLite table (`person_profiles`) with `person_id` as the primary key.
- Set `UNISON_CONTEXT_PROFILE_KEY` to a base64 url-safe Fernet key to encrypt/decrypt stored profiles. Without it, profiles are stored as JSON.
- Enable `UNISON_REQUIRE_CONSENT=true` to require consent scopes on profile endpoints; access is further restricted to roles `admin|operator|service`.
- Orchestrator skills such as `person.enroll`, `person.update_prefs`, and the startup prompt planner call the `/profile/{person_id}` APIs to read/write preferences (locale, dashboard, voice, payments, policy group, and similar fields). BCI profiles can be stored under a `bci` block (devices, control scheme, thresholds, decoder params, calibration/model pointers) with calibration artifacts kept in `unison-storage` vault.
- Copy `.env.example` to `.env` and adjust hosts/keys for your setup.

### Testing
- Create venv and install deps: `python3 -m venv .venv && . .venv/bin/activate && pip install -c ../constraints.txt -r requirements.txt`
- Run tests (with plugins disabled): `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OTEL_SDK_DISABLED=true python -m pytest -p no:capture -p no:logging tests`

### Quickstart
```bash
cp .env.example .env
python src/context_service.py
```

### Key Endpoints (sample)
- `GET /health` and `GET /ready` â€” service status
- `GET /profile/{person_id}` â€” fetch profile
- `POST /profile/{person_id}` â€” write profile (honors `UNISON_REQUIRE_CONSENT` and role checks)
- `POST /kv/put` â€” store arbitrary key/value pairs
- `POST /kv/get` â€” retrieve stored values
- `GET /dashboard/{person_id}` â€” fetch per-person dashboard state (cards + preferences)
- `POST /dashboard/{person_id}` â€” store per-person dashboard state (encrypted when profile key is configured)

```bash
curl -X POST http://localhost:8081/profile/person-123 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"profile": {"name": "Alex", "locale": "en-US", "dashboard": {"theme": "high-contrast"}}}'

curl -X POST http://localhost:8081/dashboard/person-123 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"dashboard": {"cards": [{"id": "card-1", "type": "summary", "title": "Morning briefing", "body": "3 meetings today."}], "preferences": {"layout": "comms-first"}}}'
```

## Docs

Full docs at https://project-unisonos.github.io
