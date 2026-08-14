"""SQLite/Postgres-backed governed context repository for Phase 2."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import Engine, bindparam, inspect, text

from unison_common.governed_context import (
    Commitment,
    ContextSpace,
    Goal,
    MemberRole,
    MemoryGovernance,
    MemoryKind,
    MemoryRecord,
    PersonalCharter,
    Relationship,
    SemanticPrivacyState,
    SpaceKind,
    SpaceMembership,
)
from unison_common.governed_memory import (
    AuthorizedContextPacket,
    DataDomainDefinition,
    DerivedViewDescriptor,
    DerivedViewInvalidationReceipt,
    MemoryRetrievalRequest,
    TaxonomyActivationReceipt,
    TaxonomyDecision,
    TaxonomyMigrationCommand,
    TaxonomyMigrationPreview,
    TaxonomyMigrationReceipt,
    TaxonomyProposal,
    TaxonomyProposalPreview,
    TaxonomyRollbackReceipt,
    TaxonomySecurityReview,
    TaxonomyUsageSignal,
    taxonomy_preview_digest,
)
from unison_common.household import (
    CoordinationAction,
    CoordinationStatus,
    HouseholdArtifact,
    HouseholdArtifactKind,
    HouseholdCoordinationOutcome,
    HouseholdCoordinationRequest,
    SharePreview,
    SharedFact,
)


class ContextAccessDenied(RuntimeError):
    pass


class AmbiguousContext(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


class GovernedContextRepository:
    """Authoritative local repository. Relationship edges never grant access."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.migrate()

    def migrate(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS context_spaces (
                space_id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_person_id TEXT NOT NULL,
                household_id TEXT, assistant_instance_id TEXT, name TEXT NOT NULL, purpose TEXT NOT NULL,
                key_handle TEXT NOT NULL, key_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, deleted_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS space_memberships (
                membership_id TEXT PRIMARY KEY, space_id TEXT NOT NULL, person_id TEXT NOT NULL,
                role TEXT NOT NULL, state TEXT NOT NULL, invited_by TEXT,
                created_at TEXT NOT NULL, accepted_at TEXT, removed_at TEXT,
                UNIQUE(space_id, person_id)
            )""",
            """CREATE TABLE IF NOT EXISTS relationships (
                relationship_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL,
                subject_id TEXT NOT NULL, label TEXT NOT NULL, context_tags_json TEXT NOT NULL,
                provenance TEXT NOT NULL, confidence REAL NOT NULL,
                created_at TEXT NOT NULL, deleted_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS memory_records (
                record_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL, space_id TEXT NOT NULL,
                kind TEXT NOT NULL, content_json TEXT NOT NULL, provenance TEXT NOT NULL,
                source_record_id TEXT, relationship_ids_json TEXT NOT NULL,
                governance_json TEXT NOT NULL, confidence REAL NOT NULL, revision INTEGER NOT NULL,
                deletion_state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS memory_record_history (
                history_id TEXT PRIMARY KEY, record_id TEXT NOT NULL, revision INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL, changed_by TEXT NOT NULL, reason TEXT NOT NULL,
                changed_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS personal_charters (
                charter_id TEXT PRIMARY KEY, person_id TEXT NOT NULL UNIQUE,
                principles_json TEXT NOT NULL, prohibited_json TEXT NOT NULL, origin TEXT NOT NULL,
                revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS charter_history (
                history_id TEXT PRIMARY KEY, charter_id TEXT NOT NULL, revision INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL, changed_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS goals (
                goal_id TEXT PRIMARY KEY, person_id TEXT NOT NULL, space_id TEXT NOT NULL,
                title TEXT NOT NULL, origin TEXT NOT NULL, status TEXT NOT NULL,
                revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS commitments (
                commitment_id TEXT PRIMARY KEY, person_id TEXT NOT NULL, space_id TEXT NOT NULL,
                title TEXT NOT NULL, origin TEXT NOT NULL, due_at TEXT, state TEXT NOT NULL,
                revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS governed_audit (
                audit_id TEXT PRIMARY KEY, actor_person_id TEXT NOT NULL, action TEXT NOT NULL,
                space_id TEXT, record_id TEXT, purpose TEXT NOT NULL,
                detail_json TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS governed_migration_journal (
                person_id TEXT NOT NULL, source_table TEXT NOT NULL,
                migrated_at TEXT NOT NULL, record_count INTEGER NOT NULL,
                PRIMARY KEY (person_id, source_table)
            )""",
            """CREATE TABLE IF NOT EXISTS derived_memory_views (
                view_id TEXT PRIMARY KEY, source_record_id TEXT NOT NULL,
                source_revision INTEGER NOT NULL, space_id TEXT NOT NULL,
                descriptor_json TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, invalidated_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS derived_view_invalidation_receipts (
                receipt_id TEXT PRIMARY KEY, view_id TEXT NOT NULL,
                source_record_id TEXT NOT NULL, receipt_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS taxonomy_usage_signals (
                signal_id TEXT NOT NULL, owner_person_id TEXT NOT NULL,
                candidate_domain_id TEXT NOT NULL, signal_type TEXT NOT NULL,
                suggested_level TEXT NOT NULL, observed_at TEXT NOT NULL, signal_json TEXT NOT NULL,
                PRIMARY KEY (owner_person_id, signal_id)
            )""",
            """CREATE TABLE IF NOT EXISTS taxonomy_proposals (
                proposal_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL,
                candidate_domain_id TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, cooldown_until TEXT, proposal_json TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS taxonomy_domains (
                owner_person_id TEXT NOT NULL, domain_id TEXT NOT NULL, level TEXT NOT NULL,
                activated_at TEXT NOT NULL, definition_json TEXT NOT NULL,
                PRIMARY KEY (owner_person_id, domain_id)
            )""",
            """CREATE TABLE IF NOT EXISTS taxonomy_decisions (
                decision_id TEXT NOT NULL, owner_person_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL, decision_json TEXT NOT NULL, decided_at TEXT NOT NULL,
                PRIMARY KEY (owner_person_id, decision_id)
            )""",
            """CREATE TABLE IF NOT EXISTS taxonomy_activation_receipts (
                receipt_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL, receipt_json TEXT NOT NULL, activated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS taxonomy_security_reviews (
                review_id TEXT NOT NULL, owner_person_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL, decision TEXT NOT NULL,
                review_json TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                PRIMARY KEY (owner_person_id, review_id)
            )""",
            """CREATE TABLE IF NOT EXISTS taxonomy_migration_previews (
                preview_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL, preview_json TEXT NOT NULL,
                expires_at TEXT NOT NULL, state TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS taxonomy_migrations (
                migration_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL, receipt_json TEXT NOT NULL,
                status TEXT NOT NULL, rollback_until TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS taxonomy_migration_items (
                migration_id TEXT NOT NULL, record_id TEXT NOT NULL,
                prior_governance_json TEXT NOT NULL, prior_revision INTEGER NOT NULL,
                PRIMARY KEY (migration_id, record_id)
            )""",
            """CREATE TABLE IF NOT EXISTS taxonomy_rollback_receipts (
                rollback_id TEXT PRIMARY KEY, owner_person_id TEXT NOT NULL,
                migration_id TEXT NOT NULL, receipt_json TEXT NOT NULL,
                rolled_back_at TEXT NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_memory_space_state
                ON memory_records(space_id, deletion_state, updated_at)""",
            """CREATE INDEX IF NOT EXISTS idx_membership_person_state
                ON space_memberships(person_id, state, space_id)""",
            """CREATE INDEX IF NOT EXISTS idx_relationship_owner_subject
                ON relationships(owner_person_id, subject_id, deleted_at)""",
            """CREATE INDEX IF NOT EXISTS idx_derived_view_source_state
                ON derived_memory_views(source_record_id, state)""",
            """CREATE INDEX IF NOT EXISTS idx_taxonomy_signal_candidate
                ON taxonomy_usage_signals(owner_person_id, candidate_domain_id, observed_at)""",
        ]
        with self.engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
        columns = {column["name"] for column in inspect(self.engine).get_columns("context_spaces")}
        if "household_id" not in columns:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE context_spaces ADD COLUMN household_id TEXT"))

    def observe_taxonomy_usage(self, actor: str, signal: TaxonomyUsageSignal) -> TaxonomyUsageSignal:
        """Persist structured evidence only; raw request or memory content is not accepted."""
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO taxonomy_usage_signals
                (signal_id, owner_person_id, candidate_domain_id, signal_type,
                 suggested_level, observed_at, signal_json)
                VALUES (:id, :owner, :candidate, :kind, :level, :observed, :payload)"""), {
                "id": signal.signal_id, "owner": actor, "candidate": signal.candidate_domain_id,
                "kind": signal.signal_type, "level": signal.suggested_level,
                "observed": signal.observed_at.isoformat(), "payload": signal.model_dump_json(),
            })
        self._audit(actor, "taxonomy.signal.observed", "taxonomy-evolution", detail={
            "candidate_domain_id": signal.candidate_domain_id, "signal_type": signal.signal_type,
        })
        return signal

    def evaluate_taxonomy_candidate(
        self, actor: str, candidate: DataDomainDefinition, proposed_level: str,
    ) -> TaxonomyProposal | None:
        if candidate.origin != "usage" or candidate.status != "proposed":
            raise ValueError("usage-driven candidates must have usage origin and proposed status")
        with self.engine.connect() as conn:
            active = conn.execute(text("""SELECT 1 FROM taxonomy_domains
                WHERE owner_person_id=:owner AND domain_id=:domain"""),
                {"owner": actor, "domain": candidate.domain_id}).fetchone()
            existing = conn.execute(text("""SELECT proposal_json FROM taxonomy_proposals
                WHERE owner_person_id=:owner AND candidate_domain_id=:domain AND status='pending'
                ORDER BY created_at DESC LIMIT 1"""),
                {"owner": actor, "domain": candidate.domain_id}).scalar()
            cooldown = conn.execute(text("""SELECT cooldown_until FROM taxonomy_proposals
                WHERE owner_person_id=:owner AND candidate_domain_id=:domain
                  AND cooldown_until IS NOT NULL ORDER BY cooldown_until DESC LIMIT 1"""),
                {"owner": actor, "domain": candidate.domain_id}).scalar()
            rows = conn.execute(text("""SELECT signal_type, suggested_level, observed_at
                FROM taxonomy_usage_signals WHERE owner_person_id=:owner AND candidate_domain_id=:domain
                ORDER BY observed_at"""), {"owner": actor, "domain": candidate.domain_id}).mappings().all()
        if active:
            return None
        if existing:
            return TaxonomyProposal.model_validate_json(existing)
        if cooldown and datetime.fromisoformat(str(cooldown)) > _now():
            return None
        matching = [row for row in rows if row["suggested_level"] == proposed_level]
        days = {datetime.fromisoformat(str(row["observed_at"])).date() for row in matching}
        if len(matching) < 3 or len(days) < 2:
            return None
        evidence_types = tuple(sorted({str(row["signal_type"]) for row in matching}))
        proposal = TaxonomyProposal(
            proposal_id=str(uuid4()), candidate=candidate, proposed_level=proposed_level,
            evidence_count=len(matching), distinct_days=len(days), evidence_types=evidence_types,
            rationale=f"Repeated usage suggests '{candidate.display_name}' may need distinct handling.",
            benefits=tuple(sorted({
                "clearer retrieval and organization",
                *( ["separate policy or key boundary"] if proposed_level == "security-domain" else []),
                *( ["more precise retention or sharing choices"] if any("friction" in item for item in evidence_types) else []),
            })),
        )
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO taxonomy_proposals
                (proposal_id, owner_person_id, candidate_domain_id, status, created_at, cooldown_until, proposal_json)
                VALUES (:id, :owner, :domain, 'pending', :created, NULL, :payload)"""), {
                "id": proposal.proposal_id, "owner": actor, "domain": candidate.domain_id,
                "created": proposal.created_at.isoformat(), "payload": proposal.model_dump_json(),
            })
        self._audit(actor, "taxonomy.proposal.created", "taxonomy-evolution", detail={
            "proposal_id": proposal.proposal_id, "candidate_domain_id": candidate.domain_id,
            "evidence_count": proposal.evidence_count,
        })
        return proposal

    def list_taxonomy_proposals(self, actor: str) -> list[TaxonomyProposal]:
        with self.engine.connect() as conn:
            values = conn.execute(text("""SELECT proposal_json FROM taxonomy_proposals
                WHERE owner_person_id=:owner ORDER BY created_at, proposal_id"""), {"owner": actor}).scalars().all()
        return [TaxonomyProposal.model_validate_json(value) for value in values]

    def list_taxonomy_domains(self, actor: str) -> list[DataDomainDefinition]:
        with self.engine.connect() as conn:
            values = conn.execute(text("""SELECT definition_json FROM taxonomy_domains
                WHERE owner_person_id=:owner ORDER BY domain_id"""), {"owner": actor}).scalars().all()
        return [DataDomainDefinition.model_validate_json(value) for value in values]

    def taxonomy_proposal_preview(self, actor: str, proposal_id: str) -> TaxonomyProposalPreview:
        proposal = self._taxonomy_proposal(actor, proposal_id)
        security = proposal.proposed_level == "security-domain"
        return TaxonomyProposalPreview(
            proposal_id=proposal.proposal_id,
            prompt=(f"You have used {proposal.candidate.display_name} often enough that it may help "
                    f"to make it a separate {'protected area' if security else 'category'}. Would you like that?"),
            summary=f"Create {proposal.candidate.display_name} as a {proposal.proposed_level}.",
            why_now=(
                f"Observed {proposal.evidence_count} relevant patterns across {proposal.distinct_days} days.",
                *tuple(f"Signal: {item.replace('-', ' ')}." for item in proposal.evidence_types),
            ),
            would_change=(
                "Future requests can use this category explicitly.",
                "Existing records change only after a separate migration preview and confirmation.",
                *( ("A separate logical key and policy boundary will be required.",) if security else ()),
            ),
            would_not_change=(
                "No records move when you approve the category.",
                "No sharing, disclosure, or remote access is enabled.",
                "You can defer or decline without limiting Unison's ability to help.",
            ),
            requires_security_review=security,
        )

    def _taxonomy_proposal(self, actor: str, proposal_id: str) -> TaxonomyProposal:
        with self.engine.connect() as conn:
            raw = conn.execute(text("""SELECT proposal_json FROM taxonomy_proposals
                WHERE proposal_id=:id AND owner_person_id=:owner"""),
                {"id": proposal_id, "owner": actor}).scalar()
        if not raw:
            raise ContextAccessDenied("taxonomy proposal is unavailable")
        return TaxonomyProposal.model_validate_json(raw)

    def record_taxonomy_security_review(
        self, actor: str, review: TaxonomySecurityReview,
    ) -> TaxonomySecurityReview:
        proposal = self._taxonomy_proposal(actor, review.proposal_id)
        if proposal.proposed_level != "security-domain":
            raise ValueError("security review applies only to security-domain proposals")
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO taxonomy_security_reviews
                (review_id, owner_person_id, proposal_id, decision, review_json, reviewed_at)
                VALUES (:id, :owner, :proposal, :decision, :payload, :at)"""), {
                "id": review.review_id, "owner": actor, "proposal": review.proposal_id,
                "decision": review.decision, "payload": review.model_dump_json(),
                "at": review.reviewed_at.isoformat(),
            })
        self._audit(actor, f"taxonomy.security-review.{review.decision}", "taxonomy-evolution",
                    detail={"proposal_id": review.proposal_id, "policy_version": review.policy_version})
        return review

    def _approved_security_review(self, actor: str, proposal_id: str) -> bool:
        with self.engine.connect() as conn:
            decision = conn.execute(text("""SELECT decision FROM taxonomy_security_reviews
                WHERE owner_person_id=:owner AND proposal_id=:proposal
                ORDER BY reviewed_at DESC LIMIT 1"""),
                {"owner": actor, "proposal": proposal_id}).scalar()
        return decision == "approve"

    def decide_taxonomy_proposal(
        self, actor: str, decision: TaxonomyDecision,
    ) -> TaxonomyActivationReceipt | None:
        with self.engine.connect() as conn:
            raw = conn.execute(text("""SELECT proposal_json FROM taxonomy_proposals
                WHERE proposal_id=:id AND owner_person_id=:owner AND status='pending'"""),
                {"id": decision.proposal_id, "owner": actor}).scalar()
        if not raw:
            raise ContextAccessDenied("taxonomy proposal is unavailable")
        proposal = TaxonomyProposal.model_validate_json(raw)
        if (
            decision.decision == "approve"
            and proposal.proposed_level == "security-domain"
            and not self._approved_security_review(actor, proposal.proposal_id)
        ):
            raise ValueError("approved security review is required before security-domain activation")
        new_status = {"approve": "approved", "decline": "declined", "defer": "deferred"}[decision.decision]
        cooldown_until = None
        if decision.decision in {"decline", "defer"}:
            cooldown_until = _now() + timedelta(days=90 if decision.decision == "decline" else 30)
        updated = proposal.model_copy(update={"status": new_status, "cooldown_until": cooldown_until})
        receipt = None
        if decision.decision == "approve":
            active = proposal.candidate.model_copy(update={"status": "active"})
            receipt = TaxonomyActivationReceipt(
                receipt_id=str(uuid4()), proposal_id=proposal.proposal_id,
                decision_id=decision.decision_id, domain=active,
                migration_scope=decision.migration_scope,
            )
        with self.engine.begin() as conn:
            conn.execute(text("""UPDATE taxonomy_proposals SET status=:status,
                cooldown_until=:cooldown, proposal_json=:payload WHERE proposal_id=:id"""), {
                "status": new_status, "cooldown": cooldown_until.isoformat() if cooldown_until else None,
                "payload": updated.model_dump_json(), "id": proposal.proposal_id,
            })
            conn.execute(text("""INSERT INTO taxonomy_decisions
                (decision_id, owner_person_id, proposal_id, decision_json, decided_at)
                VALUES (:id, :owner, :proposal, :payload, :at)"""), {
                "id": decision.decision_id, "owner": actor, "proposal": proposal.proposal_id,
                "payload": decision.model_dump_json(), "at": decision.decided_at.isoformat(),
            })
            if receipt:
                conn.execute(text("""INSERT INTO taxonomy_domains
                    (owner_person_id, domain_id, level, activated_at, definition_json)
                    VALUES (:owner, :domain, :level, :at, :payload)"""), {
                    "owner": actor, "domain": receipt.domain.domain_id, "level": proposal.proposed_level,
                    "at": receipt.activated_at.isoformat(), "payload": receipt.domain.model_dump_json(),
                })
                conn.execute(text("""INSERT INTO taxonomy_activation_receipts
                    (receipt_id, owner_person_id, proposal_id, receipt_json, activated_at)
                    VALUES (:id, :owner, :proposal, :payload, :at)"""), {
                    "id": receipt.receipt_id, "owner": actor, "proposal": proposal.proposal_id,
                    "payload": receipt.model_dump_json(), "at": receipt.activated_at.isoformat(),
                })
        self._audit(actor, f"taxonomy.proposal.{new_status}", "taxonomy-evolution", detail={
            "proposal_id": proposal.proposal_id, "domain_id": proposal.candidate.domain_id,
        })
        return receipt

    def preview_taxonomy_migration(
        self, actor: str, proposal_id: str, *, source_domain_ids: Iterable[str],
        selected_record_ids: Iterable[str] = (),
    ) -> TaxonomyMigrationPreview:
        proposal = self._taxonomy_proposal(actor, proposal_id)
        if proposal.status != "approved":
            raise ValueError("taxonomy domain must be approved before migration preview")
        source_domains = tuple(sorted(set(source_domain_ids)))
        if not source_domains:
            raise ValueError("migration preview requires source domains")
        selected = tuple(sorted(set(selected_record_ids)))
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT record_id, revision, governance_json FROM memory_records
                WHERE owner_person_id=:owner AND deletion_state='active' ORDER BY record_id"""),
                {"owner": actor}).mappings().all()
        candidates = []
        for row in rows:
            governance = MemoryGovernance.model_validate(_loads(row["governance_json"], {}))
            if set(governance.data_domains).intersection(source_domains):
                candidates.append(row)
        if selected:
            candidates = [row for row in candidates if row["record_id"] in selected]
            if {row["record_id"] for row in candidates} != set(selected):
                raise ContextAccessDenied("selected migration record is unavailable")
        if not candidates:
            raise ValueError("migration preview has no eligible records")
        record_revisions = {str(row["record_id"]): int(row["revision"]) for row in candidates}
        preview_id = str(uuid4())
        created = _now()
        digest = taxonomy_preview_digest({
            "preview_id": preview_id, "proposal_id": proposal_id,
            "domain_id": proposal.candidate.domain_id, "source_domain_ids": source_domains,
            "record_revisions": record_revisions,
        })
        preview = TaxonomyMigrationPreview(
            preview_id=preview_id, proposal_id=proposal_id, domain_id=proposal.candidate.domain_id,
            source_domain_ids=source_domains, record_revisions=record_revisions,
            classification_change=f"Add {proposal.candidate.display_name} to {len(candidates)} selected record(s).",
            key_boundary_change=("Set the logical key domain to the new security domain."
                                 if proposal.proposed_level == "security-domain"
                                 else "No key-domain change."),
            created_at=created, expires_at=created + timedelta(minutes=15), confirmation_digest=digest,
        )
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO taxonomy_migration_previews
                (preview_id, owner_person_id, proposal_id, preview_json, expires_at, state)
                VALUES (:id, :owner, :proposal, :payload, :expires, 'pending')"""), {
                "id": preview.preview_id, "owner": actor, "proposal": proposal_id,
                "payload": preview.model_dump_json(), "expires": preview.expires_at.isoformat(),
            })
        return preview

    def execute_taxonomy_migration(
        self, actor: str, command: TaxonomyMigrationCommand,
    ) -> TaxonomyMigrationReceipt:
        with self.engine.connect() as conn:
            row = conn.execute(text("""SELECT preview_json, state FROM taxonomy_migration_previews
                WHERE preview_id=:id AND owner_person_id=:owner"""),
                {"id": command.preview_id, "owner": actor}).mappings().fetchone()
        if not row or row["state"] != "pending":
            raise ContextAccessDenied("migration preview is unavailable")
        preview = TaxonomyMigrationPreview.model_validate_json(row["preview_json"])
        if preview.expires_at <= _now() or command.confirmation_digest != preview.confirmation_digest:
            raise ValueError("migration preview is expired or has changed")
        proposal = self._taxonomy_proposal(actor, preview.proposal_id)
        security = proposal.proposed_level == "security-domain"
        records = [self.get_memory(actor, record_id) for record_id in preview.record_revisions]
        if any(record.revision != preview.record_revisions[record.record_id] for record in records):
            raise ValueError("migration preview is stale")
        migration_id = str(uuid4())
        completed = _now()
        receipt = TaxonomyMigrationReceipt(
            migration_id=migration_id, preview_id=preview.preview_id,
            proposal_id=preview.proposal_id, domain_id=preview.domain_id,
            migrated_record_ids=tuple(preview.record_revisions),
            rollback_until=completed + timedelta(days=30), completed_at=completed,
        )
        with self.engine.begin() as conn:
            for record in records:
                prior = record.governance
                domains = tuple(dict.fromkeys((*prior.data_domains, preview.domain_id)))
                updated = prior.model_copy(update={
                    "data_domains": domains,
                    "key_domain": preview.domain_id if security else prior.key_domain,
                })
                conn.execute(text("""INSERT INTO taxonomy_migration_items
                    (migration_id, record_id, prior_governance_json, prior_revision)
                    VALUES (:migration, :record, :governance, :revision)"""), {
                    "migration": migration_id, "record": record.record_id,
                    "governance": prior.model_dump_json(), "revision": record.revision,
                })
                conn.execute(text("""INSERT INTO memory_record_history
                    (history_id, record_id, revision, snapshot_json, changed_by, reason, changed_at)
                    VALUES (:id, :record, :revision, :snapshot, :actor, :reason, :at)"""), {
                    "id": str(uuid4()), "record": record.record_id, "revision": record.revision,
                    "snapshot": record.model_dump_json(), "actor": actor,
                    "reason": f"taxonomy migration:{migration_id}", "at": completed.isoformat(),
                })
                conn.execute(text("""UPDATE memory_records SET governance_json=:governance,
                    revision=revision+1, updated_at=:at WHERE record_id=:record"""), {
                    "governance": updated.model_dump_json(), "at": completed.isoformat(),
                    "record": record.record_id,
                })
            conn.execute(text("""INSERT INTO taxonomy_migrations
                (migration_id, owner_person_id, proposal_id, receipt_json, status, rollback_until)
                VALUES (:id, :owner, :proposal, :payload, 'complete', :until)"""), {
                "id": migration_id, "owner": actor, "proposal": preview.proposal_id,
                "payload": receipt.model_dump_json(), "until": receipt.rollback_until.isoformat(),
            })
            conn.execute(text("UPDATE taxonomy_migration_previews SET state='executed' WHERE preview_id=:id"),
                         {"id": preview.preview_id})
        for record in records:
            self.invalidate_record_views(record.record_id, "key-rotation" if security else "correction")
        self._audit(actor, "taxonomy.migration.completed", "taxonomy-evolution",
                    detail={"migration_id": migration_id, "record_count": len(records)})
        return receipt

    def rollback_taxonomy_migration(self, actor: str, migration_id: str) -> TaxonomyRollbackReceipt:
        with self.engine.connect() as conn:
            migration = conn.execute(text("""SELECT status, rollback_until FROM taxonomy_migrations
                WHERE migration_id=:id AND owner_person_id=:owner"""),
                {"id": migration_id, "owner": actor}).mappings().fetchone()
            items = conn.execute(text("""SELECT record_id, prior_governance_json FROM taxonomy_migration_items
                WHERE migration_id=:id ORDER BY record_id"""), {"id": migration_id}).mappings().all()
        if not migration or migration["status"] != "complete":
            raise ContextAccessDenied("taxonomy migration is unavailable")
        if datetime.fromisoformat(str(migration["rollback_until"])) <= _now():
            raise ValueError("taxonomy migration rollback window has closed")
        rolled_back = _now()
        receipt = TaxonomyRollbackReceipt(
            rollback_id=str(uuid4()), migration_id=migration_id,
            restored_record_ids=tuple(str(item["record_id"]) for item in items),
            rolled_back_at=rolled_back,
        )
        with self.engine.begin() as conn:
            for item in items:
                conn.execute(text("""UPDATE memory_records SET governance_json=:governance,
                    revision=revision+1, updated_at=:at
                    WHERE record_id=:record AND owner_person_id=:owner"""), {
                    "governance": item["prior_governance_json"], "at": rolled_back.isoformat(),
                    "record": item["record_id"], "owner": actor,
                })
            conn.execute(text("UPDATE taxonomy_migrations SET status='rolled-back' WHERE migration_id=:id"),
                         {"id": migration_id})
            conn.execute(text("""INSERT INTO taxonomy_rollback_receipts
                (rollback_id, owner_person_id, migration_id, receipt_json, rolled_back_at)
                VALUES (:id, :owner, :migration, :payload, :at)"""), {
                "id": receipt.rollback_id, "owner": actor, "migration": migration_id,
                "payload": receipt.model_dump_json(), "at": rolled_back.isoformat(),
            })
        for item in items:
            self.invalidate_record_views(str(item["record_id"]), "key-rotation")
        self._audit(actor, "taxonomy.migration.rolled-back", "taxonomy-evolution",
                    detail={"migration_id": migration_id, "record_count": len(items)})
        return receipt

    def _audit(
        self,
        actor: str,
        action: str,
        purpose: str,
        *,
        space_id: str | None = None,
        record_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO governed_audit
                    (audit_id, actor_person_id, action, space_id, record_id, purpose, detail_json, created_at)
                    VALUES (:id, :actor, :action, :space, :record, :purpose, :detail, :created)"""),
                {
                    "id": str(uuid4()), "actor": actor, "action": action,
                    "space": space_id, "record": record_id, "purpose": purpose,
                    "detail": json.dumps(detail or {}, sort_keys=True), "created": _iso(),
                },
            )

    def ensure_private_space(self, person_id: str, assistant_instance_id: str) -> ContextSpace:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("""SELECT space_id FROM context_spaces
                    WHERE owner_person_id=:person AND assistant_instance_id=:assistant
                    AND kind='private' AND deleted_at IS NULL"""),
                {"person": person_id, "assistant": assistant_instance_id},
            ).fetchone()
        if row:
            return self.get_space(str(row[0]))
        space = ContextSpace(
            space_id=str(uuid4()), kind=SpaceKind.PRIVATE, owner_person_id=person_id,
            assistant_instance_id=assistant_instance_id, name="Private",
            purpose="personal assistance", key_handle=f"space-key:{uuid4()}",
        )
        self._insert_space(space, owner_state="active")
        self._audit(person_id, "space.private.created", "private-space-bootstrap", space_id=space.space_id)
        return space

    def create_space(
        self, owner_person_id: str, *, name: str, purpose: str,
        household_id: str | None = None, kind: SpaceKind = SpaceKind.SHARED,
    ) -> ContextSpace:
        if kind is SpaceKind.PRIVATE:
            raise ValueError("use ensure_private_space for private spaces")
        space = ContextSpace(
            space_id=str(uuid4()), kind=kind, owner_person_id=owner_person_id,
            household_id=household_id, name=name, purpose=purpose,
            key_handle=f"space-key:{uuid4()}",
        )
        self._insert_space(space, owner_state="active")
        self._audit(owner_person_id, "space.created", purpose, space_id=space.space_id)
        return space

    def _insert_space(self, space: ContextSpace, *, owner_state: str) -> None:
        membership = SpaceMembership(
            membership_id=str(uuid4()), space_id=space.space_id,
            person_id=space.owner_person_id, role=MemberRole.OWNER,
        )
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO context_spaces
                (space_id, kind, owner_person_id, household_id, assistant_instance_id, name, purpose,
                 key_handle, key_version, created_at, deleted_at)
                VALUES (:space_id, :kind, :owner, :household, :assistant, :name, :purpose,
                        :key, :key_version, :created, NULL)"""), {
                "space_id": space.space_id, "kind": space.kind.value,
                "owner": space.owner_person_id, "household": space.household_id,
                "assistant": space.assistant_instance_id,
                "name": space.name, "purpose": space.purpose, "key": space.key_handle,
                "key_version": space.key_version, "created": space.created_at.isoformat(),
            })
            conn.execute(text("""INSERT INTO space_memberships
                (membership_id, space_id, person_id, role, state, invited_by,
                 created_at, accepted_at, removed_at)
                VALUES (:id, :space, :person, :role, :state, NULL, :created, :created, NULL)"""), {
                "id": membership.membership_id, "space": membership.space_id,
                "person": membership.person_id, "role": membership.role.value,
                "state": owner_state, "created": membership.created_at.isoformat(),
            })

    def get_space(self, space_id: str) -> ContextSpace:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM context_spaces WHERE space_id=:id"), {"id": space_id}).mappings().fetchone()
        if not row:
            raise KeyError("space not found")
        return ContextSpace(
            space_id=row["space_id"], kind=row["kind"], owner_person_id=row["owner_person_id"],
            household_id=row["household_id"],
            assistant_instance_id=row["assistant_instance_id"], name=row["name"], purpose=row["purpose"],
            key_handle=row["key_handle"], key_version=row["key_version"],
            created_at=row["created_at"], deleted_at=row["deleted_at"],
        )

    def list_spaces(self, person_id: str) -> list[ContextSpace]:
        with self.engine.connect() as conn:
            ids = conn.execute(text("""SELECT s.space_id FROM context_spaces s
                JOIN space_memberships m ON m.space_id=s.space_id
                WHERE m.person_id=:person AND m.state='active' AND s.deleted_at IS NULL
                ORDER BY s.created_at, s.space_id"""), {"person": person_id}).fetchall()
        return [self.get_space(str(row[0])) for row in ids]

    def _membership_role(self, person_id: str, space_id: str) -> MemberRole | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""SELECT role FROM space_memberships
                WHERE person_id=:person AND space_id=:space AND state='active'"""),
                {"person": person_id, "space": space_id}).fetchone()
        return MemberRole(str(row[0])) if row else None

    def require_access(self, person_id: str, space_id: str, *, write: bool = False) -> MemberRole:
        role = self._membership_role(person_id, space_id)
        if role is None or (write and role is MemberRole.VIEWER):
            raise ContextAccessDenied("context space is unavailable")
        return role

    def invite_member(self, actor: str, space_id: str, person_id: str, role: MemberRole) -> SpaceMembership:
        if self.require_access(actor, space_id, write=True) is not MemberRole.OWNER:
            raise ContextAccessDenied("context space is unavailable")
        membership = SpaceMembership(
            membership_id=str(uuid4()), space_id=space_id, person_id=person_id,
            role=role, invited_by=actor,
        )
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO space_memberships
                (membership_id, space_id, person_id, role, state, invited_by, created_at, accepted_at, removed_at)
                VALUES (:id, :space, :person, :role, 'invited', :actor, :created, NULL, NULL)
                ON CONFLICT (space_id, person_id) DO UPDATE SET role=:role, state='invited',
                    invited_by=:actor, created_at=:created, accepted_at=NULL, removed_at=NULL"""), {
                "id": membership.membership_id, "space": space_id, "person": person_id,
                "role": role.value, "actor": actor, "created": membership.created_at.isoformat(),
            })
        self._audit(actor, "membership.invited", "explicit-share", space_id=space_id, detail={"person_id": person_id, "role": role.value})
        return membership

    def accept_invitation(self, person_id: str, space_id: str) -> None:
        with self.engine.begin() as conn:
            result = conn.execute(text("""UPDATE space_memberships SET state='active', accepted_at=:now
                WHERE person_id=:person AND space_id=:space AND state='invited'"""),
                {"person": person_id, "space": space_id, "now": _iso()})
        if result.rowcount != 1:
            raise ContextAccessDenied("context space is unavailable")
        self._audit(person_id, "membership.accepted", "explicit-share", space_id=space_id)

    def remove_member(self, actor: str, space_id: str, person_id: str) -> int:
        if self.require_access(actor, space_id, write=True) is not MemberRole.OWNER or actor == person_id:
            raise ContextAccessDenied("context space is unavailable")
        with self.engine.begin() as conn:
            result = conn.execute(text("""UPDATE space_memberships SET state='removed', removed_at=:now
                WHERE space_id=:space AND person_id=:person AND state!='removed'"""),
                {"space": space_id, "person": person_id, "now": _iso()})
            if result.rowcount != 1:
                raise ContextAccessDenied("context space is unavailable")
            conn.execute(text("UPDATE context_spaces SET key_version=key_version+1 WHERE space_id=:space"), {"space": space_id})
            version = conn.execute(text("SELECT key_version FROM context_spaces WHERE space_id=:space"), {"space": space_id}).scalar_one()
        self._audit(actor, "membership.removed", "member-removal", space_id=space_id, detail={"person_id": person_id, "key_version": version})
        self.invalidate_space_views(space_id, "membership-revocation")
        return int(version)

    @staticmethod
    def _artifact_from_record(record: MemoryRecord, household_id: str) -> HouseholdArtifact:
        return HouseholdArtifact(
            artifact_id=record.record_id,
            household_id=household_id,
            space_id=record.space_id,
            kind=HouseholdArtifactKind(record.kind.value),
            created_by_person_id=record.owner_person_id,
            content=record.content,
            revision=record.revision,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def coordinate_household_artifact(
        self, actor: str, request: HouseholdCoordinationRequest
    ) -> HouseholdCoordinationOutcome:
        """Coordinate only through an explicitly shared household space."""
        write = request.action is not CoordinationAction.LIST
        self.require_access(actor, request.space_id, write=write)
        space = self.get_space(request.space_id)
        if (
            space.kind is not SpaceKind.SHARED
            or not space.household_id
            or space.household_id != request.household_id
        ):
            raise ContextAccessDenied("context space is unavailable")

        if request.action is CoordinationAction.LIST:
            kinds = (
                [MemoryKind(request.artifact_kind.value)] if request.artifact_kind else
                [MemoryKind.CALENDAR_EVENT, MemoryKind.GROCERY_ITEM]
            )
            records = self.search(actor, space_ids=[space.space_id], kinds=kinds)
            artifacts = tuple(self._artifact_from_record(item, space.household_id) for item in records)
            return HouseholdCoordinationOutcome(
                status=CoordinationStatus.COMPLETED,
                action=request.action,
                space_id=space.space_id,
                artifacts=artifacts,
                shared_facts=(SharedFact(name="artifact_count", value=len(artifacts)),),
                explanation="Only artifacts in the selected shared household space were read.",
            )

        if request.action is CoordinationAction.CREATE:
            content = (request.calendar or request.grocery).model_dump(mode="json", exclude_none=True)
            record = self.admit_memory(
                actor,
                space_id=space.space_id,
                kind=MemoryKind(request.artifact_kind.value),
                content=content,
                provenance="household-coordination",
                governance=MemoryGovernance(
                    sensitivity="household-shared",
                    data_domains=("household-shared",),
                    key_domain="household-shared",
                    purposes=(request.purpose,),
                    audiences=(f"space:{space.space_id}",),
                    allow_inference=True,
                    allow_action=True,
                    allow_disclosure=True,
                ),
            )
        else:
            current = self.get_memory(actor, str(request.artifact_id))
            if current.space_id != space.space_id or current.kind not in {
                MemoryKind.CALENDAR_EVENT, MemoryKind.GROCERY_ITEM
            }:
                raise ContextAccessDenied("context space is unavailable")
            if request.action is CoordinationAction.DELETE:
                self.delete_memory(actor, current.record_id, reason="household coordination")
                self._audit(
                    actor, "household.artifact.deleted", request.purpose,
                    space_id=space.space_id, record_id=current.record_id,
                )
                return HouseholdCoordinationOutcome(
                    status=CoordinationStatus.COMPLETED,
                    action=request.action,
                    space_id=space.space_id,
                    explanation="The shared artifact was removed without reading private memory.",
                )
            if current.kind.value != request.artifact_kind.value:
                raise ContextAccessDenied("context space is unavailable")
            content = (request.calendar or request.grocery).model_dump(mode="json", exclude_none=True)
            record = self.correct_memory(actor, current.record_id, content, "household coordination")

        artifact = self._artifact_from_record(record, space.household_id)
        self._audit(
            actor, f"household.artifact.{request.action.value}", request.purpose,
            space_id=space.space_id, record_id=record.record_id,
            detail={"kind": artifact.kind.value, "revision": artifact.revision},
        )
        return HouseholdCoordinationOutcome(
            status=CoordinationStatus.COMPLETED,
            action=request.action,
            space_id=space.space_id,
            artifact=artifact,
            shared_facts=(
                SharedFact(name="artifact_kind", value=artifact.kind.value),
                SharedFact(name="revision", value=artifact.revision),
            ),
            explanation="The outcome used only explicitly shared household facts.",
        )

    def preview_share(
        self, actor: str, record_id: str, target_space_id: str, purpose: str
    ) -> SharePreview:
        source = self.get_memory(actor, record_id)
        self.require_access(actor, target_space_id, write=True)
        target = self.get_space(target_space_id)
        if target.kind is not SpaceKind.SHARED:
            raise ContextAccessDenied("context space is unavailable")
        members = self._active_members(target_space_id)
        return SharePreview(
            source_record_id=source.record_id,
            target_space_id=target_space_id,
            target_audience=tuple(sorted(members)),
            fields_to_share=tuple(sorted(source.content)),
            purpose=purpose,
        )

    def _active_members(self, space_id: str) -> set[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT person_id FROM space_memberships
                WHERE space_id=:space AND state='active'"""), {"space": space_id}).fetchall()
        return {str(row[0]) for row in rows}

    def list_audit_events(self, actor: str, space_id: str | None = None) -> list[dict[str, Any]]:
        if space_id is not None:
            self.require_access(actor, space_id)
            allowed_spaces = {space_id}
        else:
            allowed_spaces = {space.space_id for space in self.list_spaces(actor)}
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM governed_audit ORDER BY created_at")).mappings().all()
        visible = []
        for row in rows:
            if row["actor_person_id"] != actor and row["space_id"] not in allowed_spaces:
                continue
            detail = _loads(row["detail_json"], {})
            visible.append({
                "action": row["action"],
                "purpose": row["purpose"],
                "space_id": row["space_id"],
                "record_id": row["record_id"],
                "detail": detail if row["space_id"] in allowed_spaces else {},
                "created_at": row["created_at"],
            })
        return visible

    def add_relationship(
        self, owner_person_id: str, *, subject_id: str, label: str,
        provenance: str, context_tags: Iterable[str] = (), confidence: float = 1.0,
    ) -> Relationship:
        relationship = Relationship(
            relationship_id=str(uuid4()), owner_person_id=owner_person_id,
            subject_id=subject_id, label=label, provenance=provenance,
            context_tags=tuple(context_tags), confidence=confidence,
        )
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO relationships
                (relationship_id, owner_person_id, subject_id, label, context_tags_json,
                 provenance, confidence, created_at, deleted_at)
                VALUES (:id, :owner, :subject, :label, :tags, :provenance, :confidence, :created, NULL)"""), {
                "id": relationship.relationship_id, "owner": owner_person_id,
                "subject": subject_id, "label": label, "tags": json.dumps(list(context_tags)),
                "provenance": provenance, "confidence": confidence,
                "created": relationship.created_at.isoformat(),
            })
        self._audit(owner_person_id, "relationship.created", "relationship-context", detail={"relationship_id": relationship.relationship_id, "subject_id": subject_id, "label": label})
        return relationship

    def resolve_relationship(self, owner_person_id: str, subject_id: str, label: str | None = None) -> Relationship:
        query = """SELECT * FROM relationships WHERE owner_person_id=:owner
            AND subject_id=:subject AND deleted_at IS NULL"""
        params: dict[str, Any] = {"owner": owner_person_id, "subject": subject_id}
        if label:
            query += " AND label=:label"
            params["label"] = label
        with self.engine.connect() as conn:
            rows = conn.execute(text(query), params).mappings().all()
        if not rows:
            raise KeyError("relationship not found")
        if len(rows) != 1:
            raise AmbiguousContext("multiple relationship contexts require an explicit choice")
        row = rows[0]
        return Relationship(
            relationship_id=row["relationship_id"], owner_person_id=row["owner_person_id"],
            subject_id=row["subject_id"], label=row["label"],
            context_tags=tuple(_loads(row["context_tags_json"], [])), provenance=row["provenance"],
            confidence=row["confidence"], created_at=row["created_at"], deleted_at=row["deleted_at"],
        )

    def admit_memory(
        self, actor: str, *, space_id: str, kind: MemoryKind,
        content: dict[str, Any], provenance: str,
        governance: MemoryGovernance | None = None, confidence: float = 1.0,
        relationship_ids: Iterable[str] = (), source_record_id: str | None = None,
    ) -> MemoryRecord:
        self.require_access(actor, space_id, write=True)
        space = self.get_space(space_id)
        policy = governance or MemoryGovernance()
        if space.kind is SpaceKind.EPHEMERAL:
            if policy.retention_until is None:
                raise ValueError("ephemeral memory requires retention_until")
            policy = policy.model_copy(update={"allow_backup": False, "allow_sync": False})
        record = MemoryRecord(
            record_id=str(uuid4()), owner_person_id=actor, space_id=space_id,
            kind=kind, content=content, provenance=provenance,
            source_record_id=source_record_id, relationship_ids=tuple(relationship_ids),
            governance=policy, confidence=confidence,
        )
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO memory_records
                (record_id, owner_person_id, space_id, kind, content_json, provenance,
                 source_record_id, relationship_ids_json, governance_json, confidence,
                 revision, deletion_state, created_at, updated_at)
                VALUES (:id, :owner, :space, :kind, :content, :provenance, :source,
                        :relationships, :governance, :confidence, 1, 'active', :created, :updated)"""), {
                "id": record.record_id, "owner": actor, "space": space_id,
                "kind": kind.value, "content": json.dumps(content, sort_keys=True),
                "provenance": provenance, "source": source_record_id,
                "relationships": json.dumps(list(record.relationship_ids)),
                "governance": record.governance.model_dump_json(), "confidence": confidence,
                "created": record.created_at.isoformat(), "updated": record.updated_at.isoformat(),
            })
        self._audit(actor, "memory.admitted", "memory-admission", space_id=space_id, record_id=record.record_id, detail={"kind": kind.value, "provenance": provenance})
        return record

    def _record_from_row(self, row: Any) -> MemoryRecord:
        return MemoryRecord(
            record_id=row["record_id"], owner_person_id=row["owner_person_id"],
            space_id=row["space_id"], kind=row["kind"], content=_loads(row["content_json"], {}),
            provenance=row["provenance"], source_record_id=row["source_record_id"],
            relationship_ids=tuple(_loads(row["relationship_ids_json"], [])),
            governance=MemoryGovernance.model_validate(_loads(row["governance_json"], {})),
            confidence=row["confidence"], revision=row["revision"],
            deletion_state=row["deletion_state"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def get_memory(self, actor: str, record_id: str) -> MemoryRecord:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM memory_records WHERE record_id=:id"), {"id": record_id}).mappings().fetchone()
        if not row:
            raise KeyError("record not found")
        self.require_access(actor, str(row["space_id"]))
        return self._record_from_row(row)

    def search(
        self, actor: str, *, query: str = "", space_ids: Iterable[str] | None = None,
        kinds: Iterable[MemoryKind] | None = None, data_domains: Iterable[str] | None = None,
    ) -> list[MemoryRecord]:
        requested = list(space_ids or [])
        if not requested:
            requested = [s.space_id for s in self.list_spaces(actor) if s.kind is SpaceKind.PRIVATE]
        for space_id in requested:
            self.require_access(actor, space_id)
        if not requested:
            return []
        params: dict[str, Any] = {"space_ids": requested}
        sql = "SELECT * FROM memory_records WHERE space_id IN :space_ids AND deletion_state='active'"
        expanding = [bindparam("space_ids", expanding=True)]
        kind_values = [kind.value for kind in (kinds or [])]
        if kind_values:
            params["kind_values"] = kind_values
            expanding.append(bindparam("kind_values", expanding=True))
            sql += " AND kind IN :kind_values"
        sql += " ORDER BY updated_at DESC, record_id"
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql).bindparams(*expanding), params).mappings().all()
        needle = query.casefold().strip()
        records = [self._record_from_row(row) for row in rows]
        requested_domains = frozenset(data_domains or ())
        if requested_domains:
            records = [
                record for record in records
                if requested_domains.intersection(record.governance.data_domains)
            ]
        if needle:
            records = [record for record in records if needle in json.dumps(record.content, sort_keys=True).casefold()]
        return records

    def build_prompt_context(self, actor: str, *, space_ids: Iterable[str], query: str, purpose: str) -> dict[str, Any]:
        requested = tuple(space_ids)
        if not requested:
            raise AmbiguousContext("an explicit context space is required")
        packet = self.retrieve_context(actor, MemoryRetrievalRequest(
            space_ids=requested, data_domains=("core-private",),
            query=query, purpose=purpose,
        ))
        spaces = [self.get_space(space_id) for space_id in packet.space_ids]
        privacy = SemanticPrivacyState(
            active_space_ids=packet.space_ids,
            space_kinds=tuple(space.kind for space in spaces),
            purpose=purpose, contains_inferences=packet.contains_inferences,
            disclosure_allowed=packet.disclosure_allowed,
        )
        return {
            "records": list(packet.records),
            "privacy": privacy.model_dump(mode="json"),
            "context_packet": packet.model_dump(mode="json"),
        }

    def retrieve_context(self, actor: str, request: MemoryRetrievalRequest) -> AuthorizedContextPacket:
        requested = tuple(request.space_ids)
        records = self.search(
            actor, query=request.query, space_ids=requested,
            data_domains=request.data_domains,
        )
        for space_id in requested:
            self.get_space(space_id)
        allowed = [
            record
            for record in records
            if record.governance.allow_inference
            and (not record.governance.purposes or request.purpose in record.governance.purposes)
        ]
        bounded: list[MemoryRecord] = []
        used_tokens = 0
        for record in allowed:
            estimate = max(1, len(json.dumps(record.content, sort_keys=True)) // 4)
            if used_tokens + estimate > request.token_budget:
                break
            bounded.append(record)
            used_tokens += estimate
        return AuthorizedContextPacket(
            purpose=request.purpose, space_ids=requested,
            data_domains=request.data_domains, token_budget=request.token_budget,
            records=tuple(record.model_dump(mode="json") for record in bounded),
            citations=tuple(record.provenance for record in bounded),
            contains_inferences=any(record.kind is MemoryKind.INFERRED_HYPOTHESIS for record in bounded),
            disclosure_allowed=False, remote_allowed=False,
        )

    def register_derived_view(self, actor: str, descriptor: DerivedViewDescriptor) -> DerivedViewDescriptor:
        source = self.get_memory(actor, descriptor.source_record_id)
        if (
            source.space_id != descriptor.space_id
            or source.revision != descriptor.source_revision
            or not set(descriptor.data_domains).issubset(source.governance.data_domains)
        ):
            raise ContextAccessDenied("derived view source is unavailable")
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO derived_memory_views
                (view_id, source_record_id, source_revision, space_id, descriptor_json,
                 state, created_at, invalidated_at)
                VALUES (:view, :record, :revision, :space, :descriptor, 'active', :created, NULL)"""), {
                "view": descriptor.view_id, "record": descriptor.source_record_id,
                "revision": descriptor.source_revision, "space": descriptor.space_id,
                "descriptor": descriptor.model_dump_json(), "created": descriptor.created_at.isoformat(),
            })
        return descriptor

    def _invalidate_views(self, where: str, params: dict[str, Any], reason: str) -> list[DerivedViewInvalidationReceipt]:
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT * FROM derived_memory_views WHERE state='active' AND {where}"  # nosec B608 - internal fixed clauses only
            ), params).mappings().all()
        receipts = [DerivedViewInvalidationReceipt(
            receipt_id=str(uuid4()), view_id=row["view_id"],
            source_record_id=row["source_record_id"], source_revision=row["source_revision"],
            reason=reason,
        ) for row in rows]
        with self.engine.begin() as conn:
            for receipt in receipts:
                conn.execute(text("UPDATE derived_memory_views SET state='invalidated', invalidated_at=:at WHERE view_id=:view"),
                             {"at": receipt.invalidated_at.isoformat(), "view": receipt.view_id})
                conn.execute(text("""INSERT INTO derived_view_invalidation_receipts
                    (receipt_id, view_id, source_record_id, receipt_json, created_at)
                    VALUES (:id, :view, :record, :receipt, :created)"""), {
                    "id": receipt.receipt_id, "view": receipt.view_id,
                    "record": receipt.source_record_id, "receipt": receipt.model_dump_json(),
                    "created": receipt.invalidated_at.isoformat(),
                })
        return receipts

    def invalidate_record_views(self, record_id: str, reason: str) -> list[DerivedViewInvalidationReceipt]:
        return self._invalidate_views("source_record_id=:record", {"record": record_id}, reason)

    def invalidate_space_views(self, space_id: str, reason: str) -> list[DerivedViewInvalidationReceipt]:
        return self._invalidate_views("space_id=:space", {"space": space_id}, reason)

    def invalidation_receipts(self, actor: str, record_id: str) -> list[DerivedViewInvalidationReceipt]:
        self.get_memory(actor, record_id)
        with self.engine.connect() as conn:
            rows = conn.execute(text("""SELECT receipt_json FROM derived_view_invalidation_receipts
                WHERE source_record_id=:record ORDER BY created_at, receipt_id"""), {"record": record_id}).scalars().all()
        return [DerivedViewInvalidationReceipt.model_validate_json(value) for value in rows]

    def share_memory(self, actor: str, record_id: str, target_space_id: str) -> MemoryRecord:
        source = self.get_memory(actor, record_id)
        self.require_access(actor, target_space_id, write=True)
        target = self.get_space(target_space_id)
        if target.kind is not SpaceKind.SHARED:
            raise ValueError("explicit sharing requires a shared context space")
        shared_policy = source.governance.model_copy(update={"allow_disclosure": False})
        shared = self.admit_memory(
            actor, space_id=target_space_id, kind=source.kind, content=source.content,
            provenance=f"explicit share by {actor}", governance=shared_policy,
            confidence=source.confidence, relationship_ids=source.relationship_ids,
            source_record_id=source.record_id,
        )
        self._audit(actor, "memory.shared", "explicit-share", space_id=target_space_id, record_id=shared.record_id, detail={"source_record_id": source.record_id})
        return shared

    def correct_memory(self, actor: str, record_id: str, content: dict[str, Any], reason: str) -> MemoryRecord:
        current = self.get_memory(actor, record_id)
        self.require_access(actor, current.space_id, write=True)
        snapshot = current.model_dump(mode="json")
        revision = current.revision + 1
        now = _iso()
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO memory_record_history
                (history_id, record_id, revision, snapshot_json, changed_by, reason, changed_at)
                VALUES (:id, :record, :revision, :snapshot, :actor, :reason, :now)"""), {
                "id": str(uuid4()), "record": record_id, "revision": current.revision,
                "snapshot": json.dumps(snapshot, sort_keys=True), "actor": actor,
                "reason": reason, "now": now,
            })
            conn.execute(text("""UPDATE memory_records SET content_json=:content,
                kind='user_correction', provenance=:provenance, revision=:revision, updated_at=:now
                WHERE record_id=:record"""), {
                "content": json.dumps(content, sort_keys=True), "provenance": f"correction by {actor}: {reason}",
                "revision": revision, "now": now, "record": record_id,
            })
        self._audit(actor, "memory.corrected", "user-correction", space_id=current.space_id, record_id=record_id, detail={"revision": revision, "reason": reason})
        self.invalidate_record_views(record_id, "correction")
        return self.get_memory(actor, record_id)

    def delete_memory(self, actor: str, record_id: str, reason: str = "user request") -> None:
        current = self.get_memory(actor, record_id)
        self.require_access(actor, current.space_id, write=True)
        with self.engine.begin() as conn:
            conn.execute(text("""UPDATE memory_records SET content_json='{}', deletion_state='deleted',
                revision=revision+1, updated_at=:now WHERE record_id=:record"""), {"now": _iso(), "record": record_id})
            conn.execute(
                text("UPDATE memory_record_history SET snapshot_json='{}' WHERE record_id=:record"),
                {"record": record_id},
            )
        self._audit(actor, "memory.deleted", "deletion", space_id=current.space_id, record_id=record_id, detail={"reason": reason})
        self.invalidate_record_views(record_id, "deletion")

    def reconcile_retention(self, now: datetime | None = None) -> list[str]:
        current = now or _now()
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM memory_records WHERE deletion_state='active'")).mappings().all()
        expired: list[str] = []
        for row in rows:
            policy = MemoryGovernance.model_validate(_loads(row["governance_json"], {}))
            if policy.retention_until and policy.retention_until <= current:
                with self.engine.begin() as conn:
                    conn.execute(text("""UPDATE memory_records SET content_json='{}', deletion_state='expired',
                        revision=revision+1, updated_at=:now WHERE record_id=:record"""),
                        {"now": _iso(current), "record": row["record_id"]})
                    conn.execute(
                        text("UPDATE memory_record_history SET snapshot_json='{}' WHERE record_id=:record"),
                        {"record": row["record_id"]},
                    )
                expired.append(str(row["record_id"]))
                self.invalidate_record_views(str(row["record_id"]), "retention-expiry")
        return expired

    def inspect_memory(self, actor: str, record_id: str) -> dict[str, Any]:
        record = self.get_memory(actor, record_id)
        with self.engine.connect() as conn:
            members = conn.execute(text("""SELECT person_id, role FROM space_memberships
                WHERE space_id=:space AND state='active' ORDER BY person_id"""), {"space": record.space_id}).mappings().all()
            history = conn.execute(text("""SELECT revision, changed_by, reason, changed_at
                FROM memory_record_history WHERE record_id=:record ORDER BY revision"""), {"record": record_id}).mappings().all()
        return {
            "record": record.model_dump(mode="json"),
            "why_known": record.provenance,
            "stored_in": record.space_id,
            "access": [dict(row) for row in members],
            "history": [dict(row) for row in history],
            "controls": ["correct", "delete", "share"],
        }

    def export_person(self, actor: str) -> dict[str, Any]:
        spaces = self.list_spaces(actor)
        return {
            "schema_version": 2,
            "person_id": actor,
            "exported_at": _iso(),
            "spaces": [space.model_dump(mode="json") for space in spaces],
            "records": [record.model_dump(mode="json") for space in spaces for record in self.search(actor, space_ids=[space.space_id])],
            "charter": self.get_charter(actor).model_dump(mode="json") if self._charter_exists(actor) else None,
            "goals": [goal.model_dump(mode="json") for goal in self.list_goals(actor)],
            "commitments": [item.model_dump(mode="json") for item in self.list_commitments(actor)],
        }

    def set_charter(self, actor: str, principles: Iterable[str], origin: str) -> PersonalCharter:
        existing = self.get_charter(actor) if self._charter_exists(actor) else None
        charter = PersonalCharter(
            charter_id=existing.charter_id if existing else str(uuid4()), person_id=actor,
            principles=tuple(principles), origin=origin,
            revision=(existing.revision + 1) if existing else 1,
            created_at=existing.created_at if existing else _now(), updated_at=_now(),
        )
        with self.engine.begin() as conn:
            if existing:
                conn.execute(text("""INSERT INTO charter_history
                    (history_id, charter_id, revision, snapshot_json, changed_at)
                    VALUES (:id, :charter, :revision, :snapshot, :now)"""), {
                    "id": str(uuid4()), "charter": existing.charter_id,
                    "revision": existing.revision, "snapshot": existing.model_dump_json(), "now": _iso(),
                })
            conn.execute(text("""INSERT INTO personal_charters
                (charter_id, person_id, principles_json, prohibited_json, origin, revision, created_at, updated_at)
                VALUES (:id, :person, :principles, :prohibited, :origin, :revision, :created, :updated)
                ON CONFLICT (person_id) DO UPDATE SET principles_json=:principles,
                    prohibited_json=:prohibited, origin=:origin, revision=:revision, updated_at=:updated"""), {
                "id": charter.charter_id, "person": actor, "principles": json.dumps(list(charter.principles)),
                "prohibited": json.dumps(list(charter.prohibited_objectives)), "origin": origin,
                "revision": charter.revision, "created": charter.created_at.isoformat(), "updated": charter.updated_at.isoformat(),
            })
        self._audit(actor, "charter.updated", "personal-objectives", detail={"revision": charter.revision, "origin": origin})
        return charter

    def _charter_exists(self, person_id: str) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT 1 FROM personal_charters WHERE person_id=:person"), {"person": person_id}).fetchone() is not None

    def get_charter(self, person_id: str) -> PersonalCharter:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM personal_charters WHERE person_id=:person"), {"person": person_id}).mappings().fetchone()
        if not row:
            raise KeyError("charter not found")
        return PersonalCharter(
            charter_id=row["charter_id"], person_id=row["person_id"],
            principles=tuple(_loads(row["principles_json"], [])), prohibited_objectives=tuple(_loads(row["prohibited_json"], [])),
            origin=row["origin"], revision=row["revision"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def create_goal(self, actor: str, *, space_id: str, title: str, origin: str) -> Goal:
        self.require_access(actor, space_id, write=True)
        goal = Goal(goal_id=str(uuid4()), person_id=actor, space_id=space_id, title=title, origin=origin)
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO goals
                (goal_id, person_id, space_id, title, origin, status, revision, created_at, updated_at)
                VALUES (:id, :person, :space, :title, :origin, :status, 1, :created, :updated)"""), {
                "id": goal.goal_id, "person": actor, "space": space_id, "title": title,
                "origin": origin, "status": goal.status, "created": goal.created_at.isoformat(), "updated": goal.updated_at.isoformat(),
            })
        return goal

    def list_goals(self, actor: str) -> list[Goal]:
        authorized = {space.space_id for space in self.list_spaces(actor)}
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM goals WHERE person_id=:person ORDER BY created_at"), {"person": actor}).mappings().all()
        return [Goal(**dict(row)) for row in rows if row["space_id"] in authorized]

    def create_commitment(self, actor: str, *, space_id: str, title: str, origin: str, due_at: datetime | None = None) -> Commitment:
        self.require_access(actor, space_id, write=True)
        item = Commitment(commitment_id=str(uuid4()), person_id=actor, space_id=space_id, title=title, origin=origin, due_at=due_at)
        with self.engine.begin() as conn:
            conn.execute(text("""INSERT INTO commitments
                (commitment_id, person_id, space_id, title, origin, due_at, state, revision, created_at, updated_at)
                VALUES (:id, :person, :space, :title, :origin, :due, :state, 1, :created, :updated)"""), {
                "id": item.commitment_id, "person": actor, "space": space_id, "title": title,
                "origin": origin, "due": due_at.isoformat() if due_at else None,
                "state": item.state.value, "created": item.created_at.isoformat(), "updated": item.updated_at.isoformat(),
            })
        return item

    def list_commitments(self, actor: str) -> list[Commitment]:
        authorized = {space.space_id for space in self.list_spaces(actor)}
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM commitments WHERE person_id=:person ORDER BY created_at"), {"person": actor}).mappings().all()
        return [Commitment(**dict(row)) for row in rows if row["space_id"] in authorized]

    def migrate_legacy_private(self, person_id: str, assistant_instance_id: str) -> dict[str, int]:
        """Copy legacy profile/conversation/dashboard data into private memory only."""
        private = self.ensure_private_space(person_id, assistant_instance_id)
        counts = {"profiles": 0, "conversations": 0, "dashboards": 0}
        tables = set(inspect(self.engine).get_table_names())
        migrations = [
            ("person_profiles", "profiles", "SELECT profile_json AS payload, updated_at FROM person_profiles WHERE person_id=:person", MemoryKind.IMPORTED_DATA),
            ("conversation_sessions", "conversations", "SELECT messages_json AS payload, updated_at FROM conversation_sessions WHERE person_id=:person", MemoryKind.SUMMARY),
            ("dashboard_state", "dashboards", "SELECT state_json AS payload, updated_at FROM dashboard_state WHERE person_id=:person", MemoryKind.IMPORTED_DATA),
        ]
        for table, key, query, kind in migrations:
            if table not in tables:
                continue
            provenance = f"legacy migration:{table}"
            with self.engine.connect() as conn:
                completed = conn.execute(text("""SELECT 1 FROM governed_migration_journal
                    WHERE person_id=:person AND source_table=:table"""),
                    {"person": person_id, "table": table}).fetchone()
            if completed:
                continue
            with self.engine.connect() as conn:
                rows = conn.execute(text(query), {"person": person_id}).mappings().all()
            for row in rows:
                payload = _loads(row["payload"], {})
                content = payload if isinstance(payload, dict) else {"items": payload}
                self.admit_memory(person_id, space_id=private.space_id, kind=kind, content=content, provenance=provenance)
                counts[key] += 1
            with self.engine.begin() as conn:
                conn.execute(text("""INSERT INTO governed_migration_journal
                    (person_id, source_table, migrated_at, record_count)
                    VALUES (:person, :table, :now, :count)"""), {
                    "person": person_id, "table": table, "now": _iso(), "count": counts[key],
                })
        return counts
