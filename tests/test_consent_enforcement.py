import pytest
from fastapi.testclient import TestClient
from fastapi import Request
import httpx

from unison_common.consent import ConsentScopes, clear_consent_cache
import os, sys


def make_consent_app():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.post("/introspect")
    async def introspect(request: Request):
        body = await request.json()
        token = body.get("token")
        if token == "valid-read":
            return JSONResponse({"active": True, "sub": "user1", "scopes": [ConsentScopes.REPLAY_READ]})
        if token == "valid-write":
            return JSONResponse({"active": True, "sub": "user1", "scopes": [ConsentScopes.INGEST_WRITE]})
        if token == "admin":
            return JSONResponse({"active": True, "sub": "admin", "scopes": [ConsentScopes.ADMIN_ALL]})
        if token == "inactive":
            return JSONResponse({"active": False})
        return JSONResponse({"active": True, "scopes": []})

    return app


def test_context_kv_consent_enforced(monkeypatch):
    monkeypatch.setenv("UNISON_REQUIRE_CONSENT", "true")
    clear_consent_cache()
    consent_app = make_consent_app()
    consent_transport = httpx.ASGITransport(app=consent_app)

    orig_async_client = httpx.AsyncClient

    def _patched_async_client(*args, **kwargs):
        kwargs.setdefault("transport", consent_transport)
        return orig_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched_async_client)

    client = TestClient(context_app)

    # GET requires REPLAY_READ
    r_forbidden = client.post("/kv/get", json={"keys": ["p1:profile:a"]}, headers={"Authorization": "Bearer none"})
    assert r_forbidden.status_code == 403

    r_ok = client.post("/kv/get", json={"keys": ["p1:profile:a"]}, headers={"Authorization": "Bearer valid-read"})
    assert r_ok.status_code == 200

    # PUT requires INGEST_WRITE
    r_forbidden_put = client.post(
        "/kv/put",
        json={"person_id": "p1", "tier": "B", "items": {"p1:profile:a": 1}},
        headers={"Authorization": "Bearer none"},
    )
    assert r_forbidden_put.status_code == 403

    r_ok_put = client.post(
        "/kv/put",
        json={"person_id": "p1", "tier": "B", "items": {"p1:profile:a": 1}},
        headers={"Authorization": "Bearer valid-write"},
    )
    assert r_ok_put.status_code == 200
