from fastapi import FastAPI, Request, Body, Depends
import uvicorn
import logging
import json
import time
import sqlite3
from base64 import urlsafe_b64decode
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import quote
from unison_common.logging import configure_logging, log_json
from unison_common.tracing_middleware import TracingMiddleware
from unison_common.tracing import initialize_tracing, instrument_fastapi, instrument_httpx
from unison_common.http_client import http_put_json_with_retry, http_get_json_with_retry
from unison_common.consent import require_consent, ConsentScopes
from unison_common.auth import require_roles
from redaction import redact
try:
    from unison_common import BatonMiddleware
except Exception:  # optional
    BatonMiddleware = None
from collections import defaultdict
from fastapi import Header

import httpx
from fastapi import APIRouter

# Import settings as a top-level module since PYTHONPATH is set in the Dockerfile
from settings import ContextServiceSettings

app = FastAPI(title="unison-context")
app.add_middleware(TracingMiddleware, service_name="unison-context")
if BatonMiddleware:
    app.add_middleware(BatonMiddleware)

_KV_STORE: Dict[str, Any] = {}
# Routers are declared up-front so they can be referenced by later route decorators.
conv_router = APIRouter()
profile_router = APIRouter()
dashboard_router = APIRouter()

logger = configure_logging("unison-context")

# P0.3: Initialize tracing and instrument FastAPI/httpx
initialize_tracing()
instrument_fastapi(app)
instrument_httpx()

# Simple in-memory metrics and caches
_metrics = defaultdict(int)
_start_time = time.time()
_conversation_store: Dict[str, Dict[str, Any]] = {}
_DB_CONN: sqlite3.Connection = None
_DB_PATH: Path = Path("/tmp/unison-context-conversation.db")
_PROFILE_KEY: Optional[bytes] = None
_DASHBOARD_MAX = 100


def load_settings() -> ContextServiceSettings:
    """Load settings from environment and refresh global shortcuts."""
    settings = ContextServiceSettings.from_env()
    globals()["SETTINGS"] = settings
    globals()["STORAGE_HOST"] = settings.storage.host
    globals()["STORAGE_PORT"] = settings.storage.port
    globals()["POLICY_HOST"] = settings.policy.host
    globals()["POLICY_PORT"] = settings.policy.port
    globals()["POLICY_VALIDATE"] = settings.policy.enable_validation
    globals()["REQUIRE_CONSENT"] = settings.require_consent
    globals()["_DB_PATH"] = Path(settings.conversation_db_path)
    globals()["_PROFILE_KEY"] = _load_profile_key(settings.profile_enc_key)
    return settings


def storage_put(key: str, value: Any) -> bool:
    ok, _, _ = http_put_json_with_retry(STORAGE_HOST, STORAGE_PORT, f"/kv/context/{quote(key, safe='')}", {"value": value}, max_retries=3, base_delay=0.1, max_delay=2.0, timeout=2.0)
    return ok

def storage_get(key: str) -> Any:
    ok, _, body = http_get_json_with_retry(STORAGE_HOST, STORAGE_PORT, f"/kv/context/{quote(key, safe='')}", max_retries=3, base_delay=0.1, max_delay=2.0, timeout=2.0)
    if not ok or not isinstance(body, dict):
        return None
    return body.get("value")


def _init_db():
    """Initialize SQLite for conversation storage."""
    global _DB_CONN
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DB_CONN = sqlite3.connect(_DB_PATH, check_same_thread=False)
    _DB_CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_sessions (
            person_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            messages_json TEXT,
            response_json TEXT,
            summary TEXT,
            updated_at REAL,
            PRIMARY KEY (person_id, session_id)
        )
        """
    )
    _DB_CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS person_profiles (
            person_id TEXT PRIMARY KEY,
            profile_json TEXT,
            updated_at REAL
        )
        """
    )
    _DB_CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS dashboard_state (
            person_id TEXT PRIMARY KEY,
            state_json TEXT,
            updated_at REAL
        )
        """
    )
    _DB_CONN.execute("PRAGMA journal_mode=WAL;")
    _DB_CONN.commit()


def _load_profile_key(raw: str) -> Optional[bytes]:
    if not raw:
        return None
    try:
        return urlsafe_b64decode(raw)
    except Exception:
        return None


def _encrypt_profile(profile: Dict[str, Any]) -> str:
    if not _PROFILE_KEY:
        return json.dumps(profile)
    try:
        from cryptography.fernet import Fernet

        f = Fernet(_PROFILE_KEY)
        return f.encrypt(json.dumps(profile).encode("utf-8")).decode("utf-8")
    except Exception:
        return json.dumps(profile)


def _decrypt_profile(ciphertext: str) -> Dict[str, Any]:
    if not _PROFILE_KEY:
        return json.loads(ciphertext) if ciphertext else {}
    try:
        from cryptography.fernet import Fernet

        f = Fernet(_PROFILE_KEY)
        plaintext = f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        return json.loads(plaintext)
    except Exception:
        return json.loads(ciphertext) if ciphertext else {}


def _encrypt_dashboard(state: Dict[str, Any]) -> str:
    if not _PROFILE_KEY:
        return json.dumps(state)
    try:
        from cryptography.fernet import Fernet

        f = Fernet(_PROFILE_KEY)
        return f.encrypt(json.dumps(state).encode("utf-8")).decode("utf-8")
    except Exception:
        return json.dumps(state)


def _decrypt_dashboard(ciphertext: str) -> Dict[str, Any]:
    if not _PROFILE_KEY:
        return json.loads(ciphertext) if ciphertext else {}
    try:
        from cryptography.fernet import Fernet

        f = Fernet(_PROFILE_KEY)
        plaintext = f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        return json.loads(plaintext)
    except Exception:
        return json.loads(ciphertext) if ciphertext else {}


# Initialize settings after helpers are defined
load_settings()
_init_db()


def _validate_policy_group(policy_group: str) -> bool:
    if not POLICY_VALIDATE:
        return True
    try:
        ok, status, body = http_get_json_with_retry(
            POLICY_HOST,
            POLICY_PORT,
            f"/groups/{quote(policy_group, safe='')}",
            max_retries=1,
            timeout=2.0,
        )
        return ok and status == 200
    except Exception:
        return False


def _sanitize_payments(payments: Dict[str, Any]) -> Dict[str, Any]:
    """Remove unexpected fields from payments profile data."""
    if not isinstance(payments, dict):
        return {}
    instruments = payments.get("instruments", [])
    cleaned_instruments = []
    allowed_keys = {
        "instrument_id",
        "provider",
        "kind",
        "display_name",
        "brand",
        "last4",
        "expiry",
        "handle",
        "vault_key",
        "created_at",
    }
    for item in instruments if isinstance(instruments, list) else []:
        if not isinstance(item, dict):
            continue
        entry = {k: item.get(k) for k in allowed_keys if k in item}
        # Basic type normalization; avoid storing overly sensitive metadata.
        if entry.get("last4") and isinstance(entry["last4"], str):
            entry["last4"] = entry["last4"][-4:]
        cleaned_instruments.append(entry)
    result = {"instruments": cleaned_instruments}
    # Optional preferences like defaults/limits can pass through if shaped safely.
    prefs = payments.get("preferences")
    if isinstance(prefs, dict):
        result["preferences"] = prefs
    return result


# Minimal header-based auth helpers (used for tests/local dev)
def _authorize(headers: Dict[str, Any]):
    """Minimal header-based allow for tests; real deployments should rely on service tokens."""
    role = headers.get("x-test-role")
    if role and role in {"admin", "operator", "service"}:
        return True
    return False


def _role_guard(x_test_role: Optional[str] = Header(default=None)):
    if _authorize({"x-test-role": x_test_role}):
        return {"roles": [x_test_role]}
    # In production, this should be replaced by proper auth; here we return None to trigger 401
    from fastapi import HTTPException

    raise HTTPException(status_code=401, detail="unauthorized")


# --- Dashboard state ---
@dashboard_router.get("/dashboard/{person_id}")
def dashboard_get(
    person_id: str,
    consent=Depends(require_consent([ConsentScopes.INGEST_READ])) if REQUIRE_CONSENT else None,
    current_user: Dict[str, Any] = Depends(_role_guard),
):
    if not isinstance(person_id, str) or not person_id:
        return {"ok": False, "error": "invalid-person-id"}
    try:
        row = _DB_CONN.execute(
            "SELECT state_json, updated_at FROM dashboard_state WHERE person_id=?", (person_id,)
        ).fetchone()
        if not row:
            return {"ok": True, "dashboard": None}
        state_json, updated_at = row
        state = _decrypt_dashboard(state_json) if state_json else {}
        return {"ok": True, "dashboard": state, "updated_at": updated_at}
    except Exception as exc:
        log_json(logging.WARNING, "dashboard_get_error", service="unison-context", error=str(exc))
        return {"ok": False, "error": "dashboard-fetch-failed"}


@dashboard_router.post("/dashboard/{person_id}")
def dashboard_put(
    person_id: str,
    body: Dict[str, Any] = Body(...),
    consent=Depends(require_consent([ConsentScopes.INGEST_WRITE])) if REQUIRE_CONSENT else None,
    current_user: Dict[str, Any] = Depends(_role_guard),
):
    if not isinstance(person_id, str) or not person_id:
        return {"ok": False, "error": "invalid-person-id"}
    dashboard = body.get("dashboard")
    if not isinstance(dashboard, dict):
        return {"ok": False, "error": "invalid-dashboard"}
    try:
        state_json = _encrypt_dashboard(dashboard)
        _DB_CONN.execute(
            "REPLACE INTO dashboard_state (person_id, state_json, updated_at) VALUES (?, ?, ?)",
            (person_id, state_json, time.time()),
        )
        _DB_CONN.commit()
        return {"ok": True, "person_id": person_id}
    except Exception as exc:
        log_json(logging.WARNING, "dashboard_put_error", service="unison-context", error=str(exc))
        return {"ok": False, "error": "dashboard-store-failed"}

@app.get("/healthz")
@app.get("/health")
def health(request: Request):
    _metrics["/health"] += 1
    event_id = request.headers.get("X-Event-ID")
    log_json(logging.INFO, "health", service="unison-context", event_id=event_id)
    return {"status": "ok", "service": "unison-context"}

@app.get("/metrics")
def metrics():
    """Prometheus text-format metrics."""
    uptime = time.time() - _start_time
    lines = [
        "# HELP unison_context_requests_total Total number of requests by endpoint",
        "# TYPE unison_context_requests_total counter",
    ]
    for k, v in _metrics.items():
        lines.append(f'unison_context_requests_total{{endpoint="{k}"}} {v}')
    lines.extend([
        "",
        "# HELP unison_context_uptime_seconds Service uptime in seconds",
        "# TYPE unison_context_uptime_seconds gauge",
        f"unison_context_uptime_seconds {uptime}",
        "",
        "# HELP unison_context_kv_size Number of items in in-memory KV store",
        "# TYPE unison_context_kv_size gauge",
        f"unison_context_kv_size {len(_KV_STORE)}",
    ])
    return "\n".join(lines)

@app.get("/readyz")
@app.get("/ready")
def ready(request: Request):
    event_id = request.headers.get("X-Event-ID")
    # Check downstream Storage health
    try:
        ok, status_code, _ = http_get_json_with_retry(
            STORAGE_HOST,
            STORAGE_PORT,
            "/health",
            headers={"X-Event-ID": event_id},
            max_retries=1,
            timeout=2.0,
        )
        storage_ok = ok and status_code == 200
    except Exception:
        storage_ok = False
    ready = storage_ok
    log_json(logging.INFO, "ready", service="unison-context", event_id=event_id, storage_ok=storage_ok, ready=ready)
    return {"ready": ready, "storage": {"host": STORAGE_HOST, "port": STORAGE_PORT, "ok": storage_ok}}

# --- Conversation storage (companion loop) ---
@conv_router.get("/conversation/health")
def conversation_health():
    try:
        _DB_CONN.execute("SELECT 1")
        return {"ok": True, "db_path": str(_DB_PATH)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

@conv_router.post("/conversation/{person_id}/{session_id}")
def conversation_store(person_id: str, session_id: str, body: Dict[str, Any] = Body(...)):
    """
    Store conversational turns for a person/session.
    Body: { messages: [...], response: {...}, summary: str }
    """
    key = f"{person_id}:{session_id}"
    messages = body.get("messages") or []
    response = body.get("response") or {}
    summary = body.get("summary") or ""
    _conversation_store[key] = {
        "messages": messages,
        "response": response,
        "summary": summary,
        "updated_at": time.time(),
    }
    # Persist to SQLite
    try:
        _DB_CONN.execute(
            "REPLACE INTO conversation_sessions (person_id, session_id, messages_json, response_json, summary, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                person_id,
                session_id,
                json.dumps(messages),
                json.dumps(response),
                summary,
                time.time(),
            ),
        )
        _DB_CONN.commit()
    except Exception as exc:
        log_json(logging.WARNING, "conversation_store_db_error", service="unison-context", error=str(exc))
    return {"ok": True, "event_id": key}

@conv_router.get("/conversation/{person_id}/{session_id}")
def conversation_load(person_id: str, session_id: str):
    key = f"{person_id}:{session_id}"
    # Prefer in-memory; otherwise attempt storage load
    if key in _conversation_store:
        return _conversation_store[key]
    try:
        row = _DB_CONN.execute(
            "SELECT messages_json, response_json, summary, updated_at FROM conversation_sessions WHERE person_id=? AND session_id=?",
            (person_id, session_id),
        ).fetchone()
        if row:
            messages_json, response_json, summary, updated_at = row
            stored = {
                "messages": json.loads(messages_json) if messages_json else [],
                "response": json.loads(response_json) if response_json else {},
                "summary": summary,
                "updated_at": updated_at,
            }
            _conversation_store[key] = stored
            return stored
    except Exception as exc:
        log_json(logging.WARNING, "conversation_load_db_error", service="unison-context", error=str(exc))
    return {"messages": []}

app.include_router(conv_router)


# --- Person profile storage ---
@profile_router.get("/profile/{person_id}")
def profile_get(
    person_id: str,
    consent=Depends(require_consent([ConsentScopes.INGEST_READ])) if REQUIRE_CONSENT else None,
    current_user: Dict[str, Any] = Depends(_role_guard),
):
    if not isinstance(person_id, str) or not person_id:
        return {"ok": False, "error": "invalid-person-id"}
    try:
        row = _DB_CONN.execute(
            "SELECT profile_json, updated_at FROM person_profiles WHERE person_id=?", (person_id,)
        ).fetchone()
        if not row:
            return {"ok": True, "profile": None}
        profile_json, updated_at = row
        profile = _decrypt_profile(profile_json) if profile_json else {}
        redacted = redact(profile)
        return {"ok": True, "profile": profile, "profile_redacted": redacted, "updated_at": updated_at}
    except Exception as exc:
        log_json(logging.WARNING, "profile_get_error", service="unison-context", error=str(exc))
        return {"ok": False, "error": "profile-fetch-failed"}


@profile_router.post("/profile/{person_id}")
def profile_put(
    person_id: str,
    body: Dict[str, Any] = Body(...),
    consent=Depends(require_consent([ConsentScopes.INGEST_WRITE])) if REQUIRE_CONSENT else None,
    current_user: Dict[str, Any] = Depends(_role_guard),
):
    if not isinstance(person_id, str) or not person_id:
        return {"ok": False, "error": "invalid-person-id"}
    profile = body.get("profile")
    if not isinstance(profile, dict):
        return {"ok": False, "error": "invalid-profile"}
    # Optional: policy group check - if profile contains policy_group, ensure caller is allowed
    policy_group = profile.get("policy_group")
    if policy_group and POLICY_VALIDATE:
        valid = _validate_policy_group(policy_group)
        if not valid:
            return {"ok": False, "error": "invalid-policy-group"}
    if "payments" in profile:
        profile["payments"] = _sanitize_payments(profile.get("payments"))
    try:
        stored = _encrypt_profile(profile)
        _DB_CONN.execute(
            "REPLACE INTO person_profiles (person_id, profile_json, updated_at) VALUES (?, ?, ?)",
            (person_id, stored, time.time()),
        )
        _DB_CONN.commit()
        return {"ok": True, "person_id": person_id}
    except Exception as exc:
        log_json(logging.WARNING, "profile_put_error", service="unison-context", error=str(exc))
        return {"ok": False, "error": "profile-store-failed"}


app.include_router(profile_router)
app.include_router(dashboard_router)


@app.post("/profile.export")
def profile_export(request: Request, body: Dict[str, Any] = Body(...)):
    """Export Tier B (profile) items for a person_id.
    Body: { person_id: string }
    Returns: { ok, person_id, exported_at, items }
    """
    _metrics["/profile.export"] += 1
    event_id = request.headers.get("X-Event-ID")
    person_id = body.get("person_id")
    if not isinstance(person_id, str) or person_id == "":
        return {"ok": False, "error": "invalid-person_id", "event_id": event_id}
    prefix = f"{person_id}:"
    items: Dict[str, Any] = {}
    for k, v in list(_KV_STORE.items()):
        if isinstance(k, str) and k.startswith(prefix) and ":profile:" in k:
            items[k] = v
    log_json(logging.INFO, "profile_export", service="unison-context", event_id=event_id, person_id=person_id, count=len(items))
    return {
        "ok": True,
        "person_id": person_id,
        "exported_at": time.time(),
        "items": items,
        "event_id": event_id,
    }


@app.post("/kv/put")
def kv_put(
    request: Request,
    body: Dict[str, Any] = Body(...),
    consent=Depends(require_consent([ConsentScopes.INGEST_WRITE])) if REQUIRE_CONSENT else None,
):
    """
    Stores multiple key/value pairs with person-scoped, tier-aware checks.
    Expected body: { person_id: str, tier: 'A'|'B'|'C', items: { key: value, ... } }
    Keys must be namespaced and begin with "{person_id}:". For Tier B, keys should include ":profile:".
    """
    _metrics["/kv/put"] += 1
    event_id = request.headers.get("X-Event-ID")
    person_id = body.get("person_id")
    tier = body.get("tier")
    items = body.get("items") or {}
    if not isinstance(person_id, str) or person_id == "":
        return {"ok": False, "error": "invalid-person_id", "event_id": event_id}
    if tier not in ("A", "B", "C"):
        return {"ok": False, "error": "invalid-tier", "event_id": event_id}
    if not isinstance(items, dict):
        return {"ok": False, "error": "invalid-items", "event_id": event_id}
    # Minimal namespace/tier checks
    for k in items.keys():
        if not isinstance(k, str) or not k.startswith(f"{person_id}:"):
            return {"ok": False, "error": "invalid-namespace", "key": k, "event_id": event_id}
        if tier == "B" and ":profile:" not in k:
            return {"ok": False, "error": "tier-mismatch", "key": k, "expected_segment": "profile", "event_id": event_id}
    # Persist to storage (best-effort) and update in-memory cache
    storage_ok = True
    for k, v in items.items():
        _KV_STORE[k] = v
        if not storage_put(k, v):
            storage_ok = False

    # Maintain a Tier B index for export: index:{person_id}:profile -> [keys]
    if tier == "B":
        idx_key = f"index:{person_id}:profile"
        existing = storage_get(idx_key)
        if not isinstance(existing, list):
            existing = []
        new_keys = [k for k in items.keys() if ":profile:" in k]
        merged = list({*existing, *new_keys})
        storage_put(idx_key, merged)

    log_json(logging.INFO, "kv_put", service="unison-context", event_id=event_id, person_id=person_id, tier=tier, count=len(items), storage_ok=storage_ok)
    return {"ok": True, "event_id": event_id, "count": len(items), "storage_ok": storage_ok}


@app.post("/kv/set")
def kv_set(
    request: Request,
    body: Dict[str, Any] = Body(...),
    consent=Depends(require_consent([ConsentScopes.INGEST_WRITE])) if REQUIRE_CONSENT else None,
):
    _metrics["/kv/set"] += 1
    event_id = request.headers.get("X-Event-ID")
    key = body.get("key")
    value = body.get("value")
    if not isinstance(key, str) or key == "":
        return {"ok": False, "error": "invalid-key", "event_id": event_id}
    _KV_STORE[key] = value
    log_json(logging.INFO, "kv_set", service="unison-context", event_id=event_id, key=key)
    return {"ok": True, "event_id": event_id}


@app.post("/kv/get")
def kv_get(
    request: Request,
    body: Dict[str, Any] = Body(...),
    consent=Depends(require_consent([ConsentScopes.REPLAY_READ])) if REQUIRE_CONSENT else None,
):
    _metrics["/kv/get"] += 1
    event_id = request.headers.get("X-Event-ID")
    keys: List[str] = body.get("keys") or []
    if not isinstance(keys, list):
        return {"ok": False, "error": "invalid-keys", "event_id": event_id}
    result: Dict[str, Any] = {}
    for k in keys:
        val = storage_get(k)
        if val is None and k in _KV_STORE:
            val = _KV_STORE.get(k)
        result[k] = val
    log_json(logging.INFO, "kv_get", service="unison-context", event_id=event_id, keys=len(keys))
    return {"ok": True, "values": result, "event_id": event_id}

if __name__ == "__main__":
    # Bind to the container port directly; settings currently only cover downstream deps.
    uvicorn.run(app, host="0.0.0.0", port=8081)
