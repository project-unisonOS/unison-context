from fastapi.testclient import TestClient
from src.server import app

client = TestClient(app)


def test_kv_put_rejects_invalid_person_id():
    body = {"person_id": "", "tier": "B", "items": {"x": 1}}
    r = client.post("/kv/put", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is False
    assert j.get("error") == "invalid-person_id"


def test_kv_put_rejects_invalid_tier():
    body = {"person_id": "u1", "tier": "Z", "items": {"u1:profile:k": 1}}
    r = client.post("/kv/put", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is False
    assert j.get("error") == "invalid-tier"


def test_kv_put_rejects_invalid_namespace():
    body = {"person_id": "u1", "tier": "B", "items": {"wrong:k": 1}}
    r = client.post("/kv/put", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is False
    assert j.get("error") == "invalid-namespace"


def test_kv_put_rejects_tier_b_without_profile_segment():
    body = {"person_id": "u1", "tier": "B", "items": {"u1:something:k": 1}}
    r = client.post("/kv/put", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is False
    assert j.get("error") == "tier-mismatch"


def test_kv_put_happy_path_and_get():
    # save two Tier B keys
    body = {
        "person_id": "u1",
        "tier": "B",
        "items": {
            "u1:profile:language": "en",
            "u1:profile:onboarding_complete": True,
        },
    }
    r = client.post("/kv/put", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert j.get("count") == 2

    # fetch them back
    r2 = client.post("/kv/get", json={"keys": ["u1:profile:language", "u1:profile:onboarding_complete"]})
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2.get("ok") is True
    vals = j2.get("values") or {}
    assert vals.get("u1:profile:language") == "en"
    assert vals.get("u1:profile:onboarding_complete") is True
