from fastapi import FastAPI, Request, Body, Depends
import uvicorn
import logging
import json
import time
from typing import Dict, Any, List
from urllib.parse import quote
from unison_common.logging import configure_logging, log_json
from unison_common.tracing_middleware import TracingMiddleware
from unison_common.tracing import initialize_tracing, instrument_fastapi, instrument_httpx
from unison_common.http_client import http_put_json_with_retry, http_get_json_with_retry
from unison_common.consent import require_consent, ConsentScopes
from collections import defaultdict

import httpx

from .settings import ContextServiceSettings

app = FastAPI(title="unison-context")
app.add_middleware(TracingMiddleware, service_name="unison-context")

_KV_STORE: Dict[str, Any] = {}

logger = configure_logging("unison-context")

# P0.3: Initialize tracing and instrument FastAPI/httpx
initialize_tracing()
instrument_fastapi(app)
instrument_httpx()

# Simple in-memory metrics
_metrics = defaultdict(int)
_start_time = time.time()


def load_settings() -> ContextServiceSettings:
    """Load settings from the environment and refresh global shortcuts."""
    settings = ContextServiceSettings.from_env()
    globals()["SETTINGS"] = settings
    globals()["STORAGE_HOST"] = settings.storage.host
    globals()["STORAGE_PORT"] = settings.storage.port
    globals()["REQUIRE_CONSENT"] = settings.require_consent
    return settings


load_settings()


def storage_put(key: str, value: Any) -> bool:
    ok, _, _ = http_put_json_with_retry(STORAGE_HOST, STORAGE_PORT, f"/kv/context/{quote(key, safe='')}", {"value": value}, max_retries=3, base_delay=0.1, max_delay=2.0, timeout=2.0)
    return ok

def storage_get(key: str) -> Any:
    ok, _, body = http_get_json_with_retry(STORAGE_HOST, STORAGE_PORT, f"/kv/context/{quote(key, safe='')}", max_retries=3, base_delay=0.1, max_delay=2.0, timeout=2.0)
    if not ok or not isinstance(body, dict):
        return None
    return body.get("value")

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
    uvicorn.run(app, host="0.0.0.0", port=8081)
