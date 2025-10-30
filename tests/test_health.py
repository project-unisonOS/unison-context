from fastapi.testclient import TestClient
from src.server import app


def test_health():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json().get("service") == "unison-context"
