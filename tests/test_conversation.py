import os
import sys
import tempfile
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))
import server
server._DB_CONN = None  # force re-init for isolated DB


@pytest.fixture(autouse=True)
def reset_conversation_store(monkeypatch):
    server._conversation_store.clear()
    # Isolate DB per test
    fd, path = tempfile.mkstemp()
    os.close(fd)
    # Re-init DB
    server._DB_PATH = Path(path)
    server._init_db()
    yield
    try:
        os.remove(path)
    except OSError:
        pass


def test_conversation_store_and_load_round_trip():
    client = TestClient(server.app)

    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "response": {"result": "hello"},
        "summary": "hello",
    }

    r = client.post("/conversation/p1/s1", json=body)
    assert r.status_code == 200
    payload = r.json()
    assert payload.get("ok") is True

    r2 = client.get("/conversation/p1/s1")
    assert r2.status_code == 200
    stored = r2.json()
    assert stored.get("messages") == body["messages"]
    assert stored.get("response") == body["response"]


def test_conversation_health():
    client = TestClient(server.app)
    r = client.get("/conversation/health")
    assert r.status_code == 200
    assert r.json().get("ok") is True
