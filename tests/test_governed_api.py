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
        json={"person_id": "alice", "name": "Household", "purpose": "groceries"},
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
