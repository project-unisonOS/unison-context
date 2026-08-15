import pytest
from sqlalchemy import create_engine
from resolution_repository import ResolutionAccessDenied, ResolutionRepository
from unison_common.resolution import *

def attempt(identifier, owner="alice"):
    return ResolutionAttempt(attempt_id=identifier, owner_person_id=owner, assistant_instance_id="ua",
        purpose="novel repair", risk="medium", requested_result_class="guidance",
        authorized_space_ids=("private",), authorized_domain_ids=("household",),
        budget=ResolutionBudget(time_seconds=60, model_calls=1, tool_calls=3),
        routes=(ResolutionRoute(route_id="route", kind="bounded-local-inference", state="selected"),),
        structural_fingerprint="c" * 64)

def test_person_isolation_repeat_detection_and_candidate_gate(tmp_path):
    repo = ResolutionRepository(create_engine(f"sqlite:///{tmp_path/'r.db'}", future=True))
    repo.put_attempt("alice", attempt("a1")); repo.put_attempt("alice", attempt("a2"))
    with pytest.raises(ResolutionAccessDenied): repo.get_attempt("bob", "a1")
    assert repo.repeated_fingerprints("alice") == [{"structural_fingerprint": "c" * 64, "count": 2}]
    candidate = DeterminizationCandidate(candidate_id="c1", scope="person-local", candidate_kind="skill",
        structural_fingerprint="c" * 64, evidence_attempt_ids=("a1", "a2"), invariant_steps=("inspect",),
        parameter_schema={}, authority_requirements=("person",), privacy_requirements=("local",),
        modality_requirements=("conversation", "braille"), failure_modes=("unknown",), expected_benefit="repeatability")
    repo.propose_candidate("alice", candidate)
    with pytest.raises(ValueError, match="sequential"):
        repo.transition("alice", CandidateTransition(candidate_id="c1", from_state="proposed", to_state="promoted",
            reviewer_ids=("maintainer",), package_digest="d" * 64, reason="skip"))

def test_pilot_is_opted_in_person_isolated_and_counts_incidents(tmp_path):
    repo = ResolutionRepository(create_engine(f"sqlite:///{tmp_path/'pilot.db'}", future=True))
    repo.put_attempt("alice", attempt("a1"))
    signal = ResolutionPilotSignal(signal_id="s1", attempt_id="a1", participant_id="alice",
        opted_in=True, usefulness="partly-useful", outcome="partial", elapsed_seconds=30,
        interaction_turns=2, clarification_count=1, correction_count=0,
        candidate_suggested=True, candidate_relevant=False, boundary_incident=True)
    with pytest.raises(ResolutionAccessDenied):
        repo.record_pilot_signal("bob", signal)
    repo.record_pilot_signal("alice", signal)
    assert repo.pilot_summary("alice") == {"attempts": 1, "useful_or_partial_percent": 100.0,
        "generic_refusal_percent": 0.0, "candidate_suggestions": 1,
        "candidate_precision_percent": 0.0, "boundary_incidents": 1}

def test_candidate_rejects_mixed_fingerprint_evidence(tmp_path):
    repo = ResolutionRepository(create_engine(f"sqlite:///{tmp_path/'poison.db'}", future=True))
    repo.put_attempt("alice", attempt("a1"))
    repo.put_attempt("alice", attempt("a2").model_copy(update={"structural_fingerprint": "d" * 64}))
    candidate = DeterminizationCandidate(candidate_id="c2", scope="person-local", candidate_kind="skill",
        structural_fingerprint="c" * 64, evidence_attempt_ids=("a1", "a2"), invariant_steps=("inspect",),
        parameter_schema={}, authority_requirements=("person",), privacy_requirements=("local",),
        modality_requirements=("conversation",), failure_modes=("unknown",), expected_benefit="repeatability")
    with pytest.raises(ValueError, match="fingerprint does not match"):
        repo.propose_candidate("alice", candidate)
