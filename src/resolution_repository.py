"""Person-isolated persistence for resolution attempts and skill incubation."""
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Engine, text
from unison_common.resolution import (CandidateTransition, DeterminizationCandidate,
                                      ResolutionAttempt, ResolutionPilotSignal,
                                      ResolutionReceipt)

ORDER = ["observed", "proposed", "specified", "tested", "reviewed", "signed", "canary", "promoted"]

class ResolutionAccessDenied(RuntimeError):
    pass

class ResolutionRepository:
    def __init__(self, engine: Engine):
        self.engine = engine
        with engine.begin() as conn:
            conn.execute(text("""CREATE TABLE IF NOT EXISTS resolution_attempts (
                attempt_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                state TEXT NOT NULL, attempt_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""))
            conn.execute(text("""CREATE TABLE IF NOT EXISTS resolution_receipts (
                receipt_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                receipt_json TEXT NOT NULL, completed_at TEXT NOT NULL)"""))
            conn.execute(text("""CREATE TABLE IF NOT EXISTS determinization_candidates (
                candidate_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                state TEXT NOT NULL, candidate_json TEXT NOT NULL, created_at TEXT NOT NULL)"""))
            conn.execute(text("""CREATE TABLE IF NOT EXISTS candidate_transitions (
                candidate_id TEXT NOT NULL, owner_person_id TEXT NOT NULL, transitioned_at TEXT NOT NULL,
                transition_json TEXT NOT NULL, PRIMARY KEY(candidate_id, transitioned_at))"""))
            conn.execute(text("""CREATE TABLE IF NOT EXISTS resolution_pilot_signals (
                signal_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL, signal_json TEXT NOT NULL,
                created_at TEXT NOT NULL)"""))

    def put_attempt(self, actor: str, attempt: ResolutionAttempt) -> ResolutionAttempt:
        if attempt.owner_person_id != actor:
            raise ResolutionAccessDenied("resolution attempt is unavailable")
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO resolution_attempts
                (attempt_id, owner_person_id, fingerprint, state, attempt_json, created_at, updated_at)
                VALUES (:id,:owner,:fingerprint,:state,:payload,:created,:updated)
                ON CONFLICT(attempt_id) DO UPDATE SET state=:state, attempt_json=:payload, updated_at=:updated
                WHERE owner_person_id=:owner"""), {"id": attempt.attempt_id, "owner": actor,
                "fingerprint": attempt.structural_fingerprint, "state": attempt.state,
                "payload": attempt.model_dump_json(), "created": attempt.created_at.isoformat(),
                "updated": attempt.updated_at.isoformat()})
        return attempt

    def get_attempt(self, actor: str, attempt_id: str) -> ResolutionAttempt:
        with self.engine.connect() as conn:
            raw = conn.execute(text("SELECT attempt_json FROM resolution_attempts WHERE attempt_id=:id AND owner_person_id=:owner"),
                               {"id": attempt_id, "owner": actor}).scalar()
        if not raw:
            raise ResolutionAccessDenied("resolution attempt is unavailable")
        return ResolutionAttempt.model_validate_json(raw)

    def complete(self, actor: str, receipt: ResolutionReceipt) -> ResolutionReceipt:
        attempt = self.get_attempt(actor, receipt.attempt_id)
        selected = {route.route_id for route in attempt.routes}
        if not set(receipt.selected_route_ids).issubset(selected):
            raise ValueError("receipt references an unknown route")
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO resolution_receipts VALUES (:id,:owner,:attempt,:payload,:at)"),
                {"id": receipt.receipt_id, "owner": actor, "attempt": receipt.attempt_id,
                 "payload": receipt.model_dump_json(), "at": receipt.completed_at.isoformat()})
        return receipt

    def propose_candidate(self, actor: str, candidate: DeterminizationCandidate) -> DeterminizationCandidate:
        for attempt_id in candidate.evidence_attempt_ids:
            attempt = self.get_attempt(actor, attempt_id)
            if attempt.structural_fingerprint != candidate.structural_fingerprint:
                raise ValueError("candidate evidence fingerprint does not match")
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO determinization_candidates
                (candidate_id, owner_person_id, fingerprint, state, candidate_json, created_at)
                VALUES (:id,:owner,:fingerprint,:state,:payload,:created)"""),
                {"id": candidate.candidate_id, "owner": actor, "fingerprint": candidate.structural_fingerprint,
                 "state": candidate.state, "payload": candidate.model_dump_json(), "created": candidate.created_at.isoformat()})
        return candidate

    def record_pilot_signal(self, actor: str, signal: ResolutionPilotSignal) -> ResolutionPilotSignal:
        if signal.participant_id != actor:
            raise ResolutionAccessDenied("pilot signal is unavailable")
        self.get_attempt(actor, signal.attempt_id)
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO resolution_pilot_signals
                (signal_id, owner_person_id, attempt_id, signal_json, created_at)
                VALUES (:id,:owner,:attempt,:payload,:created)"""),
                {"id": signal.signal_id, "owner": actor, "attempt": signal.attempt_id,
                 "payload": signal.model_dump_json(), "created": signal.created_at.isoformat()})
        return signal

    def pilot_summary(self, actor: str) -> dict[str, object]:
        with self.engine.connect() as conn:
            raw = conn.execute(text("""SELECT signal_json FROM resolution_pilot_signals
                WHERE owner_person_id=:owner ORDER BY created_at"""), {"owner": actor}).scalars().all()
        signals = [ResolutionPilotSignal.model_validate_json(item) for item in raw]
        suggested = [item for item in signals if item.candidate_suggested]
        def rate(count: int, total: int) -> float:
            return round(100 * count / total, 1) if total else 0.0
        return {"attempts": len(signals),
            "useful_or_partial_percent": rate(sum(item.usefulness != "not-useful" for item in signals), len(signals)),
            "generic_refusal_percent": rate(sum(item.generic_refusal for item in signals), len(signals)),
            "candidate_suggestions": len(suggested),
            "candidate_precision_percent": rate(sum(item.candidate_relevant is True for item in suggested), len(suggested)),
            "boundary_incidents": sum(item.boundary_incident for item in signals)}

    def transition(self, actor: str, transition: CandidateTransition) -> DeterminizationCandidate:
        with self.engine.connect() as conn:
            raw = conn.execute(text("SELECT candidate_json FROM determinization_candidates WHERE candidate_id=:id AND owner_person_id=:owner"),
                               {"id": transition.candidate_id, "owner": actor}).scalar()
        if not raw:
            raise ResolutionAccessDenied("candidate is unavailable")
        candidate = DeterminizationCandidate.model_validate_json(raw)
        if candidate.state != transition.from_state:
            raise ValueError("candidate transition source is stale")
        terminal = transition.to_state in {"rejected", "revoked"}
        adjacent = candidate.state in ORDER and transition.to_state in ORDER and ORDER.index(transition.to_state) == ORDER.index(candidate.state) + 1
        if not terminal and not adjacent:
            raise ValueError("candidate lifecycle transitions must be sequential")
        if transition.to_state in {"reviewed", "signed", "promoted"} and not transition.reviewer_ids:
            raise ValueError("reviewer identity is required")
        if transition.to_state in {"signed", "promoted"} and not transition.package_digest:
            raise ValueError("signed package digest is required")
        updated = candidate.model_copy(update={"state": transition.to_state,
            "executable": transition.to_state in {"signed", "canary", "promoted"}})
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE determinization_candidates SET state=:state,candidate_json=:payload WHERE candidate_id=:id"),
                         {"state": updated.state, "payload": updated.model_dump_json(), "id": updated.candidate_id})
            conn.execute(text("INSERT INTO candidate_transitions VALUES (:id,:owner,:at,:payload)"),
                         {"id": updated.candidate_id, "owner": actor, "at": transition.transitioned_at.isoformat(),
                          "payload": transition.model_dump_json()})
        return updated

    def repeated_fingerprints(self, actor: str, minimum: int = 2) -> list[dict[str, object]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT fingerprint, COUNT(*) count FROM resolution_attempts
                WHERE owner_person_id=:owner GROUP BY fingerprint HAVING COUNT(*) >= :minimum"""),
                {"owner": actor, "minimum": minimum}).mappings().all()
        return [{"structural_fingerprint": row["fingerprint"], "count": int(row["count"])} for row in rows]
