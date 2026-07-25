from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from governed_repository import ContextAccessDenied
from interaction_profiles import InteractionProfileRepository
from unison_common import SituationalOverride


def test_profile_lifecycle_and_private_boundary():
    repo = InteractionProfileRepository(create_engine("sqlite:///:memory:", future=True))
    profile = repo.propose("alice", "alice", key="output", value="conversation", origin="inferred", provenance="observed", confidence=.8)
    assert profile.effective_preferences() == {}
    approved = repo.decide("alice", "alice", profile.preferences[0].preference_id, True)
    assert approved.effective_preferences()["output"] == "conversation"
    corrected = repo.correct("alice", "alice", key="output", value="braille")
    assert corrected.effective_preferences()["output"] == "braille"
    with pytest.raises(ContextAccessDenied, match="unavailable"):
        repo.get("bob", "alice")


def test_temporary_override_expires_and_export_restore_round_trips():
    repo = InteractionProfileRepository(create_engine("sqlite:///:memory:", future=True))
    repo.propose("alice", "alice", key="detail", value="normal", origin="explicit", provenance="person")
    now = datetime.now(timezone.utc)
    profile = repo.add_override("alice", "alice", SituationalOverride(override_id="driving", key="detail", value="brief", reason="driving", expires_at=now + timedelta(minutes=2)))
    assert profile.effective_preferences(now)["detail"] == "brief"
    assert profile.effective_preferences(now + timedelta(minutes=3))["detail"] == "normal"
    exported = repo.export("alice", "alice")
    assert exported["person_id"] == "alice"
    repo.reset("alice", "alice")
    assert repo.get("alice", "alice").preferences == []
    repo.delete("alice", "alice")
    assert repo.get("alice", "alice").revision == 1
