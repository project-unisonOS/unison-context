# unison-context

## Status
Core service (active) — context/profile store used by orchestrator and renderer; exposed on `8081` in devstack.

### Profile encryption
- Set `UNISON_CONTEXT_PROFILE_KEY` to a base64 url-safe Fernet key to encrypt/decrypt stored profiles. Without it, profiles are stored as JSON.
- Enable `UNISON_REQUIRE_CONSENT=true` to require consent scopes on profile endpoints; access is further restricted to roles `admin|operator|service`.
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
- `GET /health` and `GET /ready` — service status
- `GET /profile/{person_id}` — fetch profile
- `POST /profile/{person_id}` — write profile (honors `UNISON_REQUIRE_CONSENT`)
- `POST /kv/put` — store arbitrary key/value pairs
- `POST /kv/get` — retrieve stored values

```bash
curl -X POST http://localhost:8081/profile/person-123 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Alex", "preferences": {"timezone": "UTC"}}'
```
