import pytest
from datetime import timedelta
from sqlalchemy import create_engine
from resolution_repository import ResolutionAccessDenied, ResolutionRepository
from unison_common.resolution import *
from unison_common.governed_context import utc_now

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
    repo.enroll_pilot("alice", PilotEnrollment(enrollment_id="e1", owner_person_id="alice",
        consent_grant_id="grant-1", scopes=("content-free-outcomes",), retention_days=30,
        telemetry_enabled=True))
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
    review = PilotReviewDecision(review_id="review-1", owner_person_id="alice", reviewer_id="safety",
        attempts=1, candidate_suggestions=1, boundary_incidents=1, decision="pause", reason="boundary")
    repo.record_pilot_review("alice", review)
    repo.revoke_pilot("alice")
    with pytest.raises(ResolutionAccessDenied, match="not authorized"):
        repo.record_pilot_signal("alice", signal.model_copy(update={"signal_id": "s2"}))
    assert repo.delete_pilot_data("alice").status == "deleted"
    assert repo.pilot_summary("alice")["attempts"] == 0

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

def test_headless_resume_and_synthetic_canary_are_actor_bound(tmp_path):
    repo = ResolutionRepository(create_engine(f"sqlite:///{tmp_path/'headless.db'}", future=True))
    now = utc_now()
    session = HeadlessInteractionSession(session_id="h1", owner_person_id="alice", client_id="keyboard",
        transport="lan", input_modalities=("text",), output_modalities=("visual",),
        reconnect_token_digest="sha256:" + "a" * 64, expires_at=now + timedelta(hours=1), updated_at=now)
    repo.put_headless_session("alice", session)
    assert repo.resume_headless_session("alice", "h1", "sha256:" + "a" * 64).client_id == "keyboard"
    with pytest.raises(ResolutionAccessDenied):
        repo.resume_headless_session("bob", "h1", "sha256:" + "a" * 64)
    with pytest.raises(ResolutionAccessDenied):
        repo.resume_headless_session("alice", "h1", "sha256:" + "b" * 64)

    repo.put_attempt("alice", attempt("a1")); repo.put_attempt("alice", attempt("a2"))
    candidate = DeterminizationCandidate(candidate_id="c3", scope="person-local", candidate_kind="skill",
        structural_fingerprint="c" * 64, evidence_attempt_ids=("a1", "a2"), invariant_steps=("inspect",),
        parameter_schema={}, authority_requirements=("person",), privacy_requirements=("local",),
        modality_requirements=("text",), failure_modes=("unknown",), expected_benefit="repeatability")
    repo.propose_candidate("alice", candidate)
    states = (("proposed", "specified"), ("specified", "tested"), ("tested", "reviewed"),
              ("reviewed", "signed"), ("signed", "canary"))
    for source, target in states:
        repo.transition("alice", CandidateTransition(candidate_id="c3", from_state=source, to_state=target,
            reviewer_ids=("reviewer",) if target in {"reviewed", "signed"} else (),
            package_digest="d" * 64 if target in {"signed", "canary"} else None, reason="synthetic gate"))
    canary = CandidateCanaryRecord(canary_id="canary-1", candidate_id="c3", owner_person_id="alice",
        package_digest="d" * 64, synthetic=True, authority_ids=("authority",), outcome="passed",
        prior_route_id="route")
    assert repo.record_canary("alice", canary).outcome == "passed"
