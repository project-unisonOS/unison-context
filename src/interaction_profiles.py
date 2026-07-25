from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from governed_repository import ContextAccessDenied
from unison_common import InteractionProfile, ProfilePreference, SituationalOverride


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InteractionProfileRepository:
    """Person-owned interaction profiles with reversible, auditable adaptation."""

    def __init__(self, engine: Engine):
        self.engine = engine
        with engine.begin() as conn:
            conn.execute(text("""CREATE TABLE IF NOT EXISTS interaction_profiles (
                person_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                profile_json TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""))
            conn.execute(text("""CREATE TABLE IF NOT EXISTS interaction_profile_history (
                history_id TEXT PRIMARY KEY, person_id TEXT NOT NULL,
                revision INTEGER NOT NULL, reason TEXT NOT NULL,
                profile_json TEXT NOT NULL, changed_at TEXT NOT NULL
            )"""))

    @staticmethod
    def _authorize(caller_person_id: str, person_id: str) -> None:
        if caller_person_id != person_id:
            raise ContextAccessDenied("interaction profile unavailable")

    def get(self, caller_person_id: str, person_id: str) -> InteractionProfile:
        self._authorize(caller_person_id, person_id)
        with self.engine.begin() as conn:
            row = conn.execute(text("SELECT profile_json FROM interaction_profiles WHERE person_id=:person"), {"person": person_id}).fetchone()
        return InteractionProfile.model_validate_json(row[0]) if row else InteractionProfile(person_id=person_id)

    def _save(self, profile: InteractionProfile, reason: str) -> InteractionProfile:
        updated = profile.model_copy(update={"revision": profile.revision + 1, "updated_at": _now()})
        payload = updated.model_dump_json()
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO interaction_profiles(person_id, revision, profile_json, updated_at)
                VALUES (:person, :revision, :profile, :updated)
                ON CONFLICT(person_id) DO UPDATE SET revision=:revision, profile_json=:profile, updated_at=:updated"""),
                {"person": updated.person_id, "revision": updated.revision, "profile": payload, "updated": updated.updated_at.isoformat()})
            conn.execute(text("""INSERT INTO interaction_profile_history
                (history_id, person_id, revision, reason, profile_json, changed_at)
                VALUES (:id, :person, :revision, :reason, :profile, :changed)"""),
                {"id": str(uuid4()), "person": updated.person_id, "revision": updated.revision, "reason": reason, "profile": payload, "changed": _now().isoformat()})
        return updated

    def propose(self, caller_person_id: str, person_id: str, *, key: str, value: Any, origin: str, provenance: str, confidence: float = 1.0) -> InteractionProfile:
        profile = self.get(caller_person_id, person_id)
        state = "approved" if origin in {"explicit", "migrated"} else "proposed"
        pref = ProfilePreference(preference_id=str(uuid4()), key=key, value=value, origin=origin, state=state, confidence=confidence, provenance=provenance)
        return self._save(profile.model_copy(update={"preferences": [*profile.preferences, pref]}), f"preference.{state}:{key}")

    def decide(self, caller_person_id: str, person_id: str, preference_id: str, approve: bool) -> InteractionProfile:
        profile = self.get(caller_person_id, person_id)
        found = False
        preferences = []
        for pref in profile.preferences:
            if pref.preference_id == preference_id:
                found = True
                preferences.append(pref.model_copy(update={"state": "approved" if approve else "rejected", "provenance": "person-approved" if approve else pref.provenance}))
            else:
                preferences.append(pref)
        if not found:
            raise KeyError("preference unavailable")
        return self._save(profile.model_copy(update={"preferences": preferences}), "preference.decision")

    def correct(self, caller_person_id: str, person_id: str, *, key: str, value: Any) -> InteractionProfile:
        profile = self.get(caller_person_id, person_id)
        preferences = [pref.model_copy(update={"state": "rejected"}) if pref.key == key and pref.state == "approved" else pref for pref in profile.preferences]
        replacement = ProfilePreference(preference_id=str(uuid4()), key=key, value=value, origin="explicit", provenance="person-correction")
        return self._save(profile.model_copy(update={"preferences": [*preferences, replacement]}), f"preference.corrected:{key}")

    def add_override(self, caller_person_id: str, person_id: str, override: SituationalOverride) -> InteractionProfile:
        profile = self.get(caller_person_id, person_id)
        return self._save(profile.model_copy(update={"situational_overrides": [*profile.situational_overrides, override]}), "override.added")

    def reset(self, caller_person_id: str, person_id: str) -> InteractionProfile:
        self._authorize(caller_person_id, person_id)
        current = self.get(caller_person_id, person_id)
        return self._save(InteractionProfile(person_id=person_id, revision=current.revision), "profile.reset")

    def export(self, caller_person_id: str, person_id: str) -> dict[str, Any]:
        return self.get(caller_person_id, person_id).model_dump(mode="json")

    def delete(self, caller_person_id: str, person_id: str) -> None:
        self._authorize(caller_person_id, person_id)
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM interaction_profiles WHERE person_id=:person"), {"person": person_id})
            conn.execute(text("DELETE FROM interaction_profile_history WHERE person_id=:person"), {"person": person_id})

