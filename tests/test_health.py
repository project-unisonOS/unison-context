import pathlib
import sys

from fastapi.testclient import TestClient

# Ensure src is on path when running in the image
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "src"))

from server import app  # noqa: E402


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("service") == "unison-context"
