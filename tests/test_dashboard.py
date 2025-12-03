import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))
import server  # noqa: E402


def _reset_db(tmp_path: Path):
    server._DB_CONN = None
    server._DB_PATH = tmp_path
    server._init_db()


def _make_client(tmp_path: Path) -> TestClient:
    os.environ["UNISON_REQUIRE_CONSENT"] = "false"
    os.environ["UNISON_ALLOWED_HOSTS"] = "testclient,localhost,127.0.0.1"
    _reset_db(tmp_path)
    return TestClient(server.app, headers={"x-test-role": "admin"})


def test_dashboard_put_and_get_round_trip():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    tmp_path = Path(path)
    try:
        client = _make_client(tmp_path)
        dashboard = {
            "cards": [
                {"id": "c1", "type": "summary", "title": "Morning Briefing", "body": "A short summary."},
                "ignore-me",
            ],
            "preferences": {"layout": "comms-first"},
        }

        r = client.post("/dashboard/p1", json={"dashboard": dashboard})
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True

        r2 = client.get("/dashboard/p1")
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2.get("ok") is True
        stored = body2.get("dashboard") or {}
        assert stored.get("person_id") == "p1"
        prefs = stored.get("preferences") or {}
        assert prefs.get("layout") == "comms-first"
        cards = stored.get("cards") or []
        assert isinstance(cards, list)
        # Non-dict entries should be filtered.
        assert all(isinstance(c, dict) for c in cards)
        assert any(c.get("id") == "c1" for c in cards)
        # updated_at should be present for recall/metrics.
        assert isinstance(stored.get("updated_at"), (int, float))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_dashboard_missing_returns_none():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    tmp_path = Path(path)
    try:
        client = _make_client(tmp_path)
        r = client.get("/dashboard/does-not-exist")
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("dashboard") is None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_dashboard_invalid_payload_rejected():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    tmp_path = Path(path)
    try:
        client = _make_client(tmp_path)
        # Non-dict dashboard is rejected.
        r = client.post("/dashboard/p1", json={"dashboard": "not-a-dict"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is False
        assert body.get("error") == "invalid-dashboard"

        # Cards must be a list when present.
        r2 = client.post("/dashboard/p1", json={"dashboard": {"cards": "nope"}})
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2.get("ok") is False
        assert body2.get("error") == "invalid-dashboard-cards"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_dashboard_card_limit_enforced():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    tmp_path = Path(path)
    try:
        client = _make_client(tmp_path)
        # Create more cards than the max and ensure they are trimmed.
        cards = [{"id": f"c{i}", "type": "summary"} for i in range(150)]
        r = client.post("/dashboard/p1", json={"dashboard": {"cards": cards}})
        assert r.status_code == 200
        assert r.json().get("ok") is True

        r2 = client.get("/dashboard/p1")
        assert r2.status_code == 200
        stored = (r2.json().get("dashboard")) or {}
        stored_cards = stored.get("cards") or []
        # Limit should be applied.
        assert len(stored_cards) <= server._DASHBOARD_MAX
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

