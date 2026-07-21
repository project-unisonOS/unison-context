from __future__ import annotations

from fastapi import FastAPI, Request, Body, Depends, HTTPException
import uvicorn
import logging
import json
import time
import os
from datetime import datetime
from base64 import urlsafe_b64decode
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import quote
from unison_common.logging import configure_logging, log_json
from unison_common.tracing_middleware import TracingMiddleware
from unison_common.tracing import initialize_tracing, instrument_fastapi, instrument_httpx
from unison_common.http_client import http_put_json_with_retry, http_get_json_with_retry
from unison_common.consent import require_consent, ConsentScopes
from unison_common.audit_middleware import AuditMiddleware
from unison_common.principal_middleware import (
    PrincipalBindingMiddleware,
    get_bound_principal,
    get_current_principal,
    get_current_principal_token,
)
from unison_common.trust import LocalDevelopmentKeyBroker
from redaction import redact
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
try:
    from unison_common import BatonMiddleware
except Exception:  # optional
    BatonMiddleware = None
from collections import defaultdict
from fastapi import Header

from fastapi import APIRouter

# Import settings as a top-level module since PYTHONPATH is set in the Dockerfile
from settings import DEFAULT_CONTEXT_DB_PATH, ContextServiceSettings
from governed_repository import AmbiguousContext, GovernedContextRepository
from unison_common.governed_context import MemberRole, MemoryGovernance, MemoryKind, SpaceKind

app = FastAPI(title="unison-context")
app.add_middleware(TracingMiddleware, service_name="unison-context")
if BatonMiddleware:
    app.add_middleware(BatonMiddleware)
# Audit logging with redacted headers
app.add_middleware(AuditMiddleware, service_name="unison-context")
app.add_middleware(
    PrincipalBindingMiddleware,
    service_name="context",
    public_paths={"/", "/health", "/healthz", "/ready", "/readyz", "/metrics", "/conversation/health", "/docs", "/openapi.json"},
    path_identity_patterns={
        r"/profile/(?P<person_id>[^/]+)": "person_id",
        r"/dashboard/(?P<person_id>[^/]+)": "person_id",
        r"/conversation/(?P<person_id>[^/]+)/[^/]+": "person_id",
    },
    allow_test_bypass=True,
)

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
_ENGINE: Engine | None = None
_GOVERNED: GovernedContextRepository | None = None
_DB_PATH: Path = Path(os.getenv("UNISON_CONTEXT_DB_PATH", DEFAULT_CONTEXT_DB_PATH))
_PROFILE_KEY: Optional[bytes] = None
_KEY_BROKER: Optional[LocalDevelopmentKeyBroker] = None
_DASHBOARD_MAX = 100
_DB_URL = os.getenv("UNISON_CONTEXT_DATABASE_URL")
STORAGE_HOST = ""
STORAGE_PORT = 0
POLICY_HOST = ""
POLICY_PORT = 0
POLICY_VALIDATE = False
REQUIRE_CONSENT = False


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
    globals()["_KEY_BROKER"] = LocalDevelopmentKeyBroker(globals()["_PROFILE_KEY"]) if globals()["_PROFILE_KEY"] else None
    globals()["_DB_URL"] = settings.database_url or os.getenv("UNISON_CONTEXT_DATABASE_URL")
    return settings


def storage_put(key: str, value: Any) -> bool:
    token = get_current_principal_token()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    ok, _, _ = http_put_json_with_retry(STORAGE_HOST, STORAGE_PORT, f"/kv/context/{quote(key, safe='')}", {"value": value}, headers=headers, max_retries=3, base_delay=0.1, max_delay=2.0, timeout=2.0)
    return ok

def storage_get(key: str) -> Any:
    token = get_current_principal_token()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    ok, _, body = http_get_json_with_retry(STORAGE_HOST, STORAGE_PORT, f"/kv/context/{quote(key, safe='')}", headers=headers, max_retries=3, base_delay=0.1, max_delay=2.0, timeout=2.0)
    if not ok or not isinstance(body, dict):
        return None
    return body.get("value")


def _cache_key(key: str) -> str:
    principal = get_current_principal()
    return f"{principal.cache_namespace}:{key}" if principal else key


def _index_key(key: str) -> str:
    principal = get_current_principal()
    return f"{principal.index_namespace}:{key}" if principal else key


def _init_db():
    """Initialize storage backend (Postgres via SQLAlchemy or SQLite fallback)."""
    global _ENGINE, _GOVERNED
    db_url = _DB_URL or f"sqlite:///{_DB_PATH}"
    if os.getenv("ENVIRONMENT") == "prod" and db_url.startswith("sqlite"):
        raise RuntimeError("SQLite is not allowed in production; set UNISON_CONTEXT_DATABASE_URL to Postgres")
    if db_url.startswith("sqlite:///"):
        Path(db_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    _ENGINE = create_engine(db_url, future=True)
    ddl_statements = [
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
        """,
        """
        CREATE TABLE IF NOT EXISTS person_profiles (
            person_id TEXT PRIMARY KEY,
            profile_json TEXT,
            updated_at REAL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dashboard_state (
            person_id TEXT PRIMARY KEY,
            state_json TEXT,
            updated_at REAL
        )
        """,
    ]
    with _ENGINE.begin() as conn:
        for ddl in ddl_statements:
            conn.execute(text(ddl))
    _GOVERNED = GovernedContextRepository(_ENGINE)


def _load_profile_key(raw: str) -> Optional[bytes]:
    if not raw:
        return None
    try:
        decoded = urlsafe_b64decode(raw)
        return decoded if len(decoded) >= 32 else None
    except Exception:
        return None


def _encrypt_profile(profile: Dict[str, Any]) -> str:
    principal = get_current_principal()
    if not _KEY_BROKER or not principal or not principal.key_handle:
        if os.getenv("ENVIRONMENT") == "prod":
            raise RuntimeError("principal key broker is required for profile encryption")
        return json.dumps(profile)
    encrypted = _KEY_BROKER.encrypt(
        key_handle=principal.key_handle,
        plaintext=json.dumps(profile).encode("utf-8"),
        associated_data=b"unison-context:profile",
    )
    return "p1:" + encrypted.decode("utf-8")


def _decrypt_profile(ciphertext: str) -> Dict[str, Any]:
    if not ciphertext.startswith("p1:"):
        return json.loads(ciphertext) if ciphertext else {}
    principal = get_current_principal()
    if not _KEY_BROKER or not principal or not principal.key_handle:
        raise RuntimeError("principal key broker is unavailable")
    plaintext = _KEY_BROKER.decrypt(
        key_handle=principal.key_handle,
        ciphertext=ciphertext[3:].encode("utf-8"),
        associated_data=b"unison-context:profile",
    )
    return json.loads(plaintext.decode("utf-8"))


def _encrypt_dashboard(state: Dict[str, Any]) -> str:
    principal = get_current_principal()
    if not _KEY_BROKER or not principal or not principal.key_handle:
        if os.getenv("ENVIRONMENT") == "prod":
            raise RuntimeError("principal key broker is required for dashboard encryption")
        return json.dumps(state)
    encrypted = _KEY_BROKER.encrypt(
        key_handle=principal.key_handle,
        plaintext=json.dumps(state).encode("utf-8"),
        associated_data=b"unison-context:dashboard",
    )
    return "p1:" + encrypted.decode("utf-8")


def _decrypt_dashboard(ciphertext: str) -> Dict[str, Any]:
    if not ciphertext.startswith("p1:"):
        return json.loads(ciphertext) if ciphertext else {}
    principal = get_current_principal()
    if not _KEY_BROKER or not principal or not principal.key_handle:
        raise RuntimeError("principal key broker is unavailable")
    plaintext = _KEY_BROKER.decrypt(
        key_handle=principal.key_handle,
        ciphertext=ciphertext[3:].encode("utf-8"),
        associated_data=b"unison-context:dashboard",
    )
    return json.loads(plaintext.decode("utf-8"))


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


def _role_guard(request: Request, x_test_role: Optional[str] = Header(default=None)):
    try:
        context = get_bound_principal(request)
        return {"roles": list(context.roles), "person_id": context.person_id, "principal_id": context.principal_id}
    except RuntimeError:
        pass
    if os.getenv("UNISON_PRINCIPAL_BINDING_TEST_BYPASS", "false").lower() == "true" and _authorize({"x-test-role": x_test_role}):
        return {"roles": [x_test_role]}
    from fastapi import HTTPException

    raise HTTPException(status_code=401, detail="unauthorized")


# --- Dashboard state ---
@dashboard_router.get("/dashboard/{person_id}")
def dashboard_get(
    person_id: str,
    consent=Depends(require_consent([ConsentScopes.INGEST_WRITE])) if REQUIRE_CONSENT else None,
    current_user: Dict[str, Any] = Depends(_role_guard),
):
    if not isinstance(person_id, str) or not person_id:
        return {"ok": False, "error": "invalid-person-id"}
    try:
        with _ENGINE.begin() as conn:
            row = conn.execute(
                text("SELECT state_json, updated_at FROM dashboard_state WHERE person_id=:pid"),
                {"pid": person_id},
            ).fetchone()
        if not row or not row[0]:
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
        cards = dashboard.get("cards")
        if cards is not None:
            if not isinstance(cards, list):
                return {"ok": False, "error": "invalid-dashboard-cards"}
            # Normalize to a bounded list of dicts to avoid untrusted growth.
            cleaned_cards: List[Dict[str, Any]] = []
            for card in cards:
                if isinstance(card, dict):
                    cleaned_cards.append(card)
                if len(cleaned_cards) >= _DASHBOARD_MAX:
                    break
            dashboard["cards"] = cleaned_cards
        # Stamp person_id and updated_at into the stored state when missing so callers
        # can rely on these fields without re-deriving them.
        dashboard.setdefault("person_id", person_id)
        dashboard.setdefault("updated_at", time.time())
        state_json = _encrypt_dashboard(dashboard)
        with _ENGINE.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dashboard_state (person_id, state_json, updated_at)
                    VALUES (:pid, :state_json, :updated_at)
                    ON CONFLICT (person_id) DO UPDATE SET
                        state_json=excluded.state_json,
                        updated_at=excluded.updated_at
                    """
                ),
                {"pid": person_id, "state_json": state_json, "updated_at": time.time()},
            )
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
        with _ENGINE.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "db_url": _DB_URL or f"sqlite:///{_DB_PATH}"}
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
        with _ENGINE.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO conversation_sessions (person_id, session_id, messages_json, response_json, summary, updated_at)
                    VALUES (:pid, :sid, :msg, :resp, :summary, :updated_at)
                    ON CONFLICT (person_id, session_id) DO UPDATE SET
                        messages_json=excluded.messages_json,
                        response_json=excluded.response_json,
                        summary=excluded.summary,
                        updated_at=excluded.updated_at
                    """
                ),
                {
                    "pid": person_id,
                    "sid": session_id,
                    "msg": json.dumps(messages),
                    "resp": json.dumps(response),
                    "summary": summary,
                    "updated_at": time.time(),
                },
            )
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
        with _ENGINE.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT messages_json, response_json, summary, updated_at
                    FROM conversation_sessions WHERE person_id=:pid AND session_id=:sid
                    """
                ),
                {"pid": person_id, "sid": session_id},
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
    consent=Depends(require_consent([ConsentScopes.INGEST_WRITE])) if REQUIRE_CONSENT else None,
    current_user: Dict[str, Any] = Depends(_role_guard),
):
    if not isinstance(person_id, str) or not person_id:
        return {"ok": False, "error": "invalid-person-id"}
    try:
        with _ENGINE.begin() as conn:
            row = conn.execute(
                text("SELECT profile_json, updated_at FROM person_profiles WHERE person_id=:pid"),
                {"pid": person_id},
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
    # Ensure unison_id is present for Unison-to-Unison addressing; default to person_id if missing.
    if not profile.get("unison_id"):
        profile["unison_id"] = person_id
    try:
        stored = _encrypt_profile(profile)
        with _ENGINE.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO person_profiles (person_id, profile_json, updated_at)
                    VALUES (:pid, :profile_json, :updated_at)
                    ON CONFLICT (person_id) DO UPDATE SET
                        profile_json=excluded.profile_json,
                        updated_at=excluded.updated_at
                    """
                ),
                {"pid": person_id, "profile_json": stored, "updated_at": time.time()},
            )
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
    prefix = _cache_key(f"{person_id}:")
    items: Dict[str, Any] = {}
    for k, v in list(_KV_STORE.items()):
        if isinstance(k, str) and k.startswith(prefix) and ":profile:" in k:
            items[k.removeprefix(f"{get_current_principal().cache_namespace}:") if get_current_principal() else k] = v
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
        _KV_STORE[_cache_key(k)] = v
        if not storage_put(k, v):
            storage_ok = False

    # Maintain a Tier B index for export: index:{person_id}:profile -> [keys]
    if tier == "B":
        idx_key = _index_key(f"index:{person_id}:profile")
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
    _KV_STORE[_cache_key(key)] = value
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
        partitioned = _cache_key(k)
        if val is None and partitioned in _KV_STORE:
            val = _KV_STORE.get(partitioned)
        result[k] = val
    log_json(logging.INFO, "kv_get", service="unison-context", event_id=event_id, keys=len(keys))
    return {"ok": True, "values": result, "event_id": event_id}


# --- Phase 2 governed context API ---
def _governed_actor(request: Request, supplied: str | None = None) -> tuple[str, str]:
    try:
        principal = get_bound_principal(request)
        if not principal.person_id or not principal.assistant_instance_id:
            raise HTTPException(status_code=403, detail="person authority required")
        return principal.person_id, principal.assistant_instance_id
    except RuntimeError:
        if os.getenv("UNISON_PRINCIPAL_BINDING_TEST_BYPASS", "false").lower() == "true" and supplied:
            return supplied, f"assistant-{supplied}"
        raise HTTPException(status_code=401, detail="trusted person authority required")


def _repo() -> GovernedContextRepository:
    if _GOVERNED is None:
        raise HTTPException(status_code=503, detail="governed context unavailable")
    return _GOVERNED


def _context_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AmbiguousContext):
        return HTTPException(status_code=409, detail="context choice required")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=404, detail="context unavailable")


@app.post("/v2/spaces/private")
def governed_private_space(request: Request, body: Dict[str, Any] = Body(default_factory=dict)):
    actor, assistant = _governed_actor(request, body.get("person_id"))
    space = _repo().ensure_private_space(actor, assistant)
    return {"space": space.model_dump(mode="json")}


@app.get("/v2/spaces")
def governed_list_spaces(request: Request, person_id: str | None = None):
    actor, _ = _governed_actor(request, person_id)
    return {"spaces": [item.model_dump(mode="json") for item in _repo().list_spaces(actor)]}


@app.post("/v2/spaces")
def governed_create_space(request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        space = _repo().create_space(
            actor, name=str(body.get("name") or "").strip(),
            purpose=str(body.get("purpose") or "").strip(),
            kind=SpaceKind(str(body.get("kind") or "shared")),
        )
        return {"space": space.model_dump(mode="json")}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.post("/v2/spaces/{space_id}/invitations")
def governed_invite(space_id: str, request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("actor_person_id"))
    try:
        membership = _repo().invite_member(actor, space_id, str(body["person_id"]), MemberRole(str(body.get("role") or "viewer")))
        return {"membership": membership.model_dump(mode="json"), "state": "invited"}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.post("/v2/spaces/{space_id}/invitations/accept")
def governed_accept(space_id: str, request: Request, body: Dict[str, Any] = Body(default_factory=dict)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        _repo().accept_invitation(actor, space_id)
        return {"ok": True, "space_id": space_id}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.delete("/v2/spaces/{space_id}/members/{person_id}")
def governed_remove_member(space_id: str, person_id: str, request: Request, actor_person_id: str | None = None):
    actor, _ = _governed_actor(request, actor_person_id)
    try:
        return {"ok": True, "key_version": _repo().remove_member(actor, space_id, person_id)}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.post("/v2/relationships")
def governed_relationship(request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        relationship = _repo().add_relationship(
            actor, subject_id=str(body["subject_id"]), label=str(body["label"]),
            provenance=str(body.get("provenance") or "person"),
            context_tags=body.get("context_tags") or (), confidence=float(body.get("confidence", 1.0)),
        )
        return {"relationship": relationship.model_dump(mode="json"), "grants_access": False}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.get("/v2/relationships/{subject_id}/resolve")
def governed_resolve_relationship(subject_id: str, request: Request, label: str | None = None, person_id: str | None = None):
    actor, _ = _governed_actor(request, person_id)
    try:
        item = _repo().resolve_relationship(actor, subject_id, label)
        return {"relationship": item.model_dump(mode="json"), "grants_access": False}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.post("/v2/memory")
def governed_admit_memory(request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        governance = MemoryGovernance.model_validate(body.get("governance") or {})
        record = _repo().admit_memory(
            actor, space_id=str(body["space_id"]), kind=MemoryKind(str(body["kind"])),
            content=dict(body.get("content") or {}), provenance=str(body.get("provenance") or "person"),
            governance=governance, confidence=float(body.get("confidence", 1.0)),
            relationship_ids=body.get("relationship_ids") or (),
        )
        return {"record": record.model_dump(mode="json")}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.post("/v2/memory/search")
def governed_search(request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        records = _repo().search(actor, query=str(body.get("query") or ""), space_ids=body.get("space_ids"))
        spaces = [_repo().get_space(space_id) for space_id in (body.get("space_ids") or [])]
        privacy = {
            "active_space_ids": [space.space_id for space in spaces],
            "space_kinds": [space.kind.value for space in spaces],
            "purpose": str(body.get("purpose") or "retrieval"),
            "disclosure_allowed": False,
        }
        return {"records": [record.model_dump(mode="json") for record in records], "privacy": privacy}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.post("/v2/memory/prompt-context")
def governed_prompt_context(request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        return _repo().build_prompt_context(
            actor, space_ids=body.get("space_ids") or (), query=str(body.get("query") or ""),
            purpose=str(body.get("purpose") or "answer"),
        )
    except Exception as exc:
        raise _context_error(exc) from exc


@app.post("/v2/memory/{record_id}/share")
def governed_share(record_id: str, request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        record = _repo().share_memory(actor, record_id, str(body["target_space_id"]))
        return {"record": record.model_dump(mode="json"), "source_unchanged": True}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.post("/v2/memory/{record_id}/correct")
def governed_correct(record_id: str, request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        record = _repo().correct_memory(actor, record_id, dict(body.get("content") or {}), str(body.get("reason") or "person correction"))
        return {"record": record.model_dump(mode="json")}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.delete("/v2/memory/{record_id}")
def governed_delete(record_id: str, request: Request, person_id: str | None = None):
    actor, _ = _governed_actor(request, person_id)
    try:
        _repo().delete_memory(actor, record_id)
        return {"ok": True}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.get("/v2/memory/{record_id}/inspect")
def governed_inspect(record_id: str, request: Request, person_id: str | None = None):
    actor, _ = _governed_actor(request, person_id)
    try:
        return _repo().inspect_memory(actor, record_id)
    except Exception as exc:
        raise _context_error(exc) from exc


@app.get("/v2/export")
def governed_export(request: Request, person_id: str | None = None):
    actor, _ = _governed_actor(request, person_id)
    return _repo().export_person(actor)


@app.put("/v2/charter")
def governed_charter_put(request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        charter = _repo().set_charter(actor, body.get("principles") or (), str(body.get("origin") or "person"))
        return {"charter": charter.model_dump(mode="json")}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.get("/v2/charter")
def governed_charter_get(request: Request, person_id: str | None = None):
    actor, _ = _governed_actor(request, person_id)
    try:
        return {"charter": _repo().get_charter(actor).model_dump(mode="json")}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.post("/v2/goals")
def governed_goal(request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        goal = _repo().create_goal(actor, space_id=str(body["space_id"]), title=str(body["title"]), origin=str(body.get("origin") or "person"))
        return {"goal": goal.model_dump(mode="json")}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.get("/v2/goals")
def governed_goals(request: Request, person_id: str | None = None):
    actor, _ = _governed_actor(request, person_id)
    return {"goals": [item.model_dump(mode="json") for item in _repo().list_goals(actor)]}


@app.post("/v2/commitments")
def governed_commitment(request: Request, body: Dict[str, Any] = Body(...)):
    actor, _ = _governed_actor(request, body.get("person_id"))
    try:
        due_at = body.get("due_at")
        item = _repo().create_commitment(
            actor, space_id=str(body["space_id"]), title=str(body["title"]),
            origin=str(body.get("origin") or "person"),
            due_at=datetime.fromisoformat(due_at) if due_at else None,
        )
        return {"commitment": item.model_dump(mode="json")}
    except Exception as exc:
        raise _context_error(exc) from exc


@app.get("/v2/commitments")
def governed_commitments(request: Request, person_id: str | None = None):
    actor, _ = _governed_actor(request, person_id)
    return {"commitments": [item.model_dump(mode="json") for item in _repo().list_commitments(actor)]}


@app.post("/v2/migrations/legacy-private")
def governed_migrate_legacy(request: Request, body: Dict[str, Any] = Body(default_factory=dict)):
    actor, assistant = _governed_actor(request, body.get("person_id"))
    return {"migrated": _repo().migrate_legacy_private(actor, assistant), "shared_promotions": 0}

if __name__ == "__main__":
    # Bind to the container port directly; settings currently only cover downstream deps.
    # Container ingress requires all-interface binding; network policy is enforced externally.
    uvicorn.run(app, host="0.0.0.0", port=8081)  # nosec B104
