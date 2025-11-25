import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))
import server
server._DB_CONN = None  # force re-init


@pytest.fixture(autouse=True)
def reset_profile_store():
    os.environ["UNISON_REQUIRE_CONSENT"] = "false"
    os.environ["UNISON_ALLOWED_HOSTS"] = "testclient,localhost,127.0.0.1"
    fd, path = tempfile.mkstemp()
    os.close(fd)
    server._DB_PATH = Path(path)
    server._init_db()
    yield
    try:
        os.remove(path)
    except OSError:
        pass


def test_profile_put_and_get_round_trip():
    client = TestClient(server.app, headers={"x-test-role": "admin"})

    profile = {
        "person_id": "p1",
        "auth": {"pin": "1234"},
        "preferences": {"language": "en"},
    }

    r = client.post("/profile/p1", json={"profile": profile})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True

    r2 = client.get("/profile/p1")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2.get("ok") is True
    assert body2.get("profile", {}).get("preferences", {}).get("language") == "en"


def test_profile_get_missing_returns_none():
    client = TestClient(server.app, headers={"x-test-role": "admin"})
    r = client.get("/profile/doesnotexist")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("profile") is None
