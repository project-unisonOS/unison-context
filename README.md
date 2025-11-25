# unison-context

### Profile encryption
- Set `UNISON_CONTEXT_PROFILE_KEY` to a base64 url-safe Fernet key to encrypt/decrypt stored profiles. Without it, profiles are stored as JSON.
- Enable `UNISON_REQUIRE_CONSENT=true` to require consent scopes on profile endpoints; access is further restricted to roles `admin|operator|service`.

### Testing
- Create venv and install deps: `python3 -m venv .venv && . .venv/bin/activate && pip install -c ../constraints.txt -r requirements.txt`
- Run tests (with plugins disabled): `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OTEL_SDK_DISABLED=true python -m pytest -p no:capture -p no:logging tests`
