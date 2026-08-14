from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import server
from governed_repository import GovernedContextRepository


def test_governed_api_explicit_share_and_non_oracular_denial(tmp_path, monkeypatch):
    monkeypatch.setenv("UNISON_PRINCIPAL_BINDING_TEST_BYPASS", "true")
    server._GOVERNED = GovernedContextRepository(create_engine(f"sqlite:///{tmp_path / 'api.db'}", future=True))
    client = TestClient(server.app)

    alice_private = client.post("/v2/spaces/private", json={"person_id": "alice"}).json()["space"]
    client.post("/v2/spaces/private", json={"person_id": "bob"}).raise_for_status()
    shared = client.post(
        "/v2/spaces",
        json={
            "person_id": "alice", "household_id": "household-one",
            "name": "Household", "purpose": "groceries",
        },
    ).json()["space"]
    invitation = client.post(
        f"/v2/spaces/{shared['space_id']}/invitations",
        json={"actor_person_id": "alice", "person_id": "bob", "role": "editor"},
    )
    assert invitation.json()["state"] == "invited"
    client.post(f"/v2/spaces/{shared['space_id']}/invitations/accept", json={"person_id": "bob"}).raise_for_status()

    private = client.post(
        "/v2/memory",
        json={
            "person_id": "alice", "space_id": alice_private["space_id"],
            "kind": "grocery_item", "content": {"item": "tea", "private_note": "surprise"},
            "provenance": "alice",
        },
    ).json()["record"]
    denied = client.post(
        "/v2/memory/search",
        json={"person_id": "bob", "space_ids": [alice_private["space_id"]], "query": "surprise"},
    )
    assert denied.status_code == 404
    assert denied.json()["detail"] == "context unavailable"

    clone = client.post(
        f"/v2/memory/{private['record_id']}/share",
        json={"person_id": "alice", "target_space_id": shared["space_id"]},
    ).json()
    assert clone["source_unchanged"] is True
    visible = client.post(
        "/v2/memory/search",
        json={"person_id": "bob", "space_ids": [shared["space_id"]], "query": "tea"},
    ).json()
    assert len(visible["records"]) == 1
    assert visible["privacy"]["disclosure_allowed"] is False


def test_governed_api_ambiguous_relationship_requires_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("UNISON_PRINCIPAL_BINDING_TEST_BYPASS", "true")
    server._GOVERNED = GovernedContextRepository(create_engine(f"sqlite:///{tmp_path / 'relationship.db'}", future=True))
    client = TestClient(server.app)
    for label in ("family", "business"):
        response = client.post(
            "/v2/relationships",
            json={"person_id": "alice", "subject_id": "sam", "label": label, "provenance": "alice"},
        )
        assert response.json()["grants_access"] is False
    ambiguous = client.get("/v2/relationships/sam/resolve", params={"person_id": "alice"})
    assert ambiguous.status_code == 409
    assert ambiguous.json()["detail"] == "context choice required"


def test_governed_memory_api_filters_domains_and_returns_invalidation_receipts(tmp_path, monkeypatch):
    monkeypatch.setenv("UNISON_PRINCIPAL_BINDING_TEST_BYPASS", "true")
    server._GOVERNED = GovernedContextRepository(create_engine(f"sqlite:///{tmp_path / 'memory.db'}", future=True))
    client = TestClient(server.app)
    private = client.post("/v2/spaces/private", json={"person_id": "alice"}).json()["space"]
    health = client.post("/v2/memory", json={
        "person_id": "alice", "space_id": private["space_id"], "kind": "asserted_fact",
        "content": {"value": "synthetic-health"}, "provenance": "synthetic:health",
        "governance": {"data_domains": ["health"], "key_domain": "health", "allow_inference": True},
    }).json()["record"]
    client.post("/v2/memory", json={
        "person_id": "alice", "space_id": private["space_id"], "kind": "asserted_fact",
        "content": {"value": "synthetic-financial"}, "provenance": "synthetic:financial",
        "governance": {"data_domains": ["financial"], "key_domain": "financial", "allow_inference": True},
    }).raise_for_status()

    packet = client.post("/v2/memory/retrieve", json={
        "person_id": "alice", "space_ids": [private["space_id"]],
        "data_domains": ["health"], "purpose": "answer", "query": "synthetic",
    }).json()
    assert [item["content"]["value"] for item in packet["records"]] == ["synthetic-health"]
    assert packet["remote_allowed"] is False

    client.post("/v2/memory/derived-views", json={
        "person_id": "alice", "view_id": "embedding-api-1", "view_kind": "embedding",
        "source_record_id": health["record_id"], "source_revision": health["revision"],
        "space_id": private["space_id"], "data_domains": ["health"],
        "algorithm": {"algorithm_id": "synthetic", "algorithm_version": "1"},
    }).raise_for_status()
    client.post(f"/v2/memory/{health['record_id']}/correct", json={
        "person_id": "alice", "content": {"value": "synthetic-corrected"}, "reason": "person correction",
    }).raise_for_status()
    receipts = client.get(
        f"/v2/memory/{health['record_id']}/invalidation-receipts", params={"person_id": "alice"},
    ).json()["receipts"]
    assert [(item["view_id"], item["reason"]) for item in receipts] == [("embedding-api-1", "correction")]
