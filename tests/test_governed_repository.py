from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from governed_repository import AmbiguousContext, ContextAccessDenied, GovernedContextRepository
from unison_common.governed_context import MemberRole, MemoryGovernance, MemoryKind, SpaceKind
from unison_common.household import HouseholdCoordinationRequest
from unison_common.governed_memory import (
    AlgorithmProvenance, DataDomainDefinition, DerivedViewDescriptor, MemoryRetrievalRequest,
    TaxonomyDecision, TaxonomyMigrationCommand, TaxonomySecurityReview, TaxonomyUsageSignal,
)


@pytest.fixture
def repo(tmp_path):
    return GovernedContextRepository(create_engine(f"sqlite:///{tmp_path / 'context.db'}", future=True))


def _people(repo):
    alice = repo.ensure_private_space("alice", "assistant-alice")
    bob = repo.ensure_private_space("bob", "assistant-bob")
    return alice, bob


def test_private_records_never_cross_retrieval_search_summary_index_cache_or_prompt(repo):
    alice, bob = _people(repo)
    canary = "ALICE-PRIVATE-CANARY"
    for kind in (MemoryKind.ASSERTED_FACT, MemoryKind.SUMMARY, MemoryKind.DERIVED_INDEX):
        repo.admit_memory(
            "alice", space_id=alice.space_id, kind=kind,
            content={"value": canary, "surface": kind.value}, provenance="alice",
            governance=MemoryGovernance(allow_inference=True),
        )

    assert repo.search("bob", query=canary) == []
    with pytest.raises(ContextAccessDenied):
        repo.search("bob", query=canary, space_ids=[alice.space_id])
    with pytest.raises(ContextAccessDenied):
        repo.build_prompt_context("bob", space_ids=[alice.space_id], query=canary, purpose="answer")
    assert canary not in str(repo.export_person("bob"))
    assert repo.search("alice", query=canary, space_ids=[alice.space_id])


def test_relationships_do_not_grant_access_and_overlapping_contact_prompts(repo):
    alice, bob = _people(repo)
    repo.add_relationship("bob", subject_id="alice", label="family", provenance="bob")
    repo.admit_memory("alice", space_id=alice.space_id, kind=MemoryKind.ASSERTED_FACT, content={"secret": "x"}, provenance="alice")
    with pytest.raises(ContextAccessDenied):
        repo.get_memory("bob", repo.search("alice", space_ids=[alice.space_id])[0].record_id)

    repo.add_relationship("alice", subject_id="sam", label="friend", provenance="alice")
    repo.add_relationship("alice", subject_id="sam", label="business", provenance="alice")
    with pytest.raises(AmbiguousContext):
        repo.resolve_relationship("alice", "sam")
    assert repo.resolve_relationship("alice", "sam", "business").label == "business"


def test_explicit_share_clones_without_reclassifying_private_source(repo):
    alice, _ = _people(repo)
    shared = repo.create_space(
        "alice", household_id="household-one",
        name="Household groceries", purpose="coordinate groceries"
    )
    repo.invite_member("alice", shared.space_id, "bob", MemberRole.EDITOR)
    repo.accept_invitation("bob", shared.space_id)
    private = repo.admit_memory(
        "alice", space_id=alice.space_id, kind=MemoryKind.GROCERY_ITEM,
        content={"item": "tea"}, provenance="alice",
    )
    clone = repo.share_memory("alice", private.record_id, shared.space_id)

    assert clone.record_id != private.record_id
    assert clone.source_record_id == private.record_id
    assert repo.get_memory("alice", private.record_id).space_id == alice.space_id
    assert repo.search("bob", query="tea", space_ids=[shared.space_id])[0].record_id == clone.record_id


def test_member_removal_rotates_space_key_and_revokes_all_artifacts(repo):
    _people(repo)
    shared = repo.create_space(
        "alice", household_id="household-one", name="Family", purpose="shared planning"
    )
    repo.invite_member("alice", shared.space_id, "bob", MemberRole.EDITOR)
    repo.accept_invitation("bob", shared.space_id)
    for kind in (MemoryKind.CALENDAR_EVENT, MemoryKind.SUMMARY, MemoryKind.DERIVED_INDEX):
        repo.admit_memory("alice", space_id=shared.space_id, kind=kind, content={"value": kind.value}, provenance="alice")
    assert len(repo.search("bob", space_ids=[shared.space_id])) == 3
    assert repo.remove_member("alice", shared.space_id, "bob") == 2
    with pytest.raises(ContextAccessDenied):
        repo.search("bob", space_ids=[shared.space_id])


def test_household_coordination_uses_only_shared_calendar_and_grocery_facts(repo):
    alice, _ = _people(repo)
    private = repo.admit_memory(
        "alice", space_id=alice.space_id, kind=MemoryKind.ASSERTED_FACT,
        content={"surprise": "private birthday plan"}, provenance="alice",
    )
    shared = repo.create_space(
        "alice", household_id="household-one", name="Household", purpose="coordinate"
    )
    repo.invite_member("alice", shared.space_id, "bob", MemberRole.EDITOR)
    repo.accept_invitation("bob", shared.space_id)
    created = repo.coordinate_household_artifact(
        "alice",
        HouseholdCoordinationRequest(
            household_id="household-one", space_id=shared.space_id,
            action="create", purpose="buy breakfast",
            artifact_kind="grocery_item", grocery={"item": "oats", "quantity": "1 bag"},
        ),
    )
    listed = repo.coordinate_household_artifact(
        "bob",
        HouseholdCoordinationRequest(
            household_id="household-one", space_id=shared.space_id,
            action="list", purpose="review household list",
        ),
    )
    assert created.private_sources_read == 0
    assert [item.content["item"] for item in listed.artifacts] == ["oats"]
    assert "birthday" not in str(listed.model_dump())
    assert repo.get_memory("alice", private.record_id).content["surprise"] == "private birthday plan"


def test_share_preview_and_audit_avoid_private_values(repo):
    alice, _ = _people(repo)
    shared = repo.create_space(
        "alice", household_id="household-one", name="Household", purpose="coordinate"
    )
    repo.invite_member("alice", shared.space_id, "bob", MemberRole.EDITOR)
    repo.accept_invitation("bob", shared.space_id)
    private = repo.admit_memory(
        "alice", space_id=alice.space_id, kind=MemoryKind.ASSERTED_FACT,
        content={"title": "private", "detail": "not in preview"}, provenance="alice",
    )
    preview = repo.preview_share("alice", private.record_id, shared.space_id, "coordinate")
    assert preview.source_remains_private is True
    assert preview.fields_to_share == ("detail", "title")
    assert "not in preview" not in str(preview.model_dump())
    assert repo.list_audit_events("bob", shared.space_id)


def test_retention_deletion_inspection_and_export_reconcile(repo):
    alice, _ = _people(repo)
    ephemeral = repo.create_space("alice", name="Temporary", purpose="one conversation", kind=SpaceKind.EPHEMERAL)
    expiry = datetime.now(timezone.utc) - timedelta(seconds=1)
    record = repo.admit_memory(
        "alice", space_id=ephemeral.space_id, kind=MemoryKind.ASSERTED_FACT,
        content={"temporary": "secret"}, provenance="alice",
        governance=MemoryGovernance(retention_until=expiry, allow_backup=True, allow_sync=True),
    )
    assert record.governance.allow_backup is False
    assert record.governance.allow_sync is False
    assert repo.reconcile_retention() == [record.record_id]
    assert repo.search("alice", query="secret", space_ids=[ephemeral.space_id]) == []

    durable = repo.admit_memory("alice", space_id=alice.space_id, kind=MemoryKind.ASSERTED_FACT, content={"known": "because you said so"}, provenance="conversation:1")
    view = repo.inspect_memory("alice", durable.record_id)
    assert view["why_known"] == "conversation:1"
    assert view["controls"] == ["correct", "delete", "share"]
    corrected = repo.correct_memory(
        "alice", durable.record_id, {"known": "corrected secret"}, "replace original",
    )
    repo.delete_memory("alice", corrected.record_id)
    assert "because you said so" not in str(repo.export_person("alice"))
    with repo.engine.connect() as conn:
        snapshots = conn.execute(
            text("SELECT snapshot_json FROM memory_record_history WHERE record_id=:record"),
            {"record": corrected.record_id},
        ).scalars().all()
    assert snapshots == ["{}"]


def test_correction_provenance_survives_restart_and_legacy_migration_is_private(tmp_path):
    db = tmp_path / "restart.db"
    engine = create_engine(f"sqlite:///{db}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE person_profiles (person_id TEXT PRIMARY KEY, profile_json TEXT, updated_at REAL)"))
        conn.execute(text("INSERT INTO person_profiles VALUES ('alice', '{\"name\": \"Alice\"}', 1)"))
    repo = GovernedContextRepository(engine)
    private = repo.ensure_private_space("alice", "assistant-alice")
    assert repo.migrate_legacy_private("alice", "assistant-alice")["profiles"] == 1
    imported = repo.search("alice", query="Alice", space_ids=[private.space_id])[0]
    corrected = repo.correct_memory("alice", imported.record_id, {"name": "Alicia"}, "preferred name")

    restarted = GovernedContextRepository(create_engine(f"sqlite:///{db}", future=True))
    restored = restarted.get_memory("alice", corrected.record_id)
    assert restored.content == {"name": "Alicia"}
    assert restored.revision == 2
    assert restored.provenance.startswith("correction by alice")
    assert restarted.inspect_memory("alice", restored.record_id)["history"][0]["reason"] == "preferred name"
    assert restarted.migrate_legacy_private("alice", "assistant-alice") == {"profiles": 0, "conversations": 0, "dashboards": 0}


def test_charter_goals_commitments_and_shared_artifacts_have_origins(repo):
    private, _ = _people(repo)
    charter = repo.set_charter("alice", ["Protect my time", "Support family joy"], "alice")
    revised = repo.set_charter("alice", ["Protect my time", "Prefer local privacy"], "alice correction")
    goal = repo.create_goal("alice", space_id=private.space_id, title="Call family weekly", origin="alice")
    commitment = repo.create_commitment("alice", space_id=private.space_id, title="Call Sunday", origin="calendar import")
    calendar = repo.admit_memory("alice", space_id=private.space_id, kind=MemoryKind.CALENDAR_EVENT, content={"title": "Family call"}, provenance="calendar import")
    grocery = repo.admit_memory("alice", space_id=private.space_id, kind=MemoryKind.GROCERY_ITEM, content={"item": "coffee"}, provenance="alice")

    assert charter.revision == 1 and revised.revision == 2
    assert repo.list_goals("alice")[0].origin == goal.origin
    assert repo.list_commitments("alice")[0].origin == commitment.origin
    assert {calendar.kind, grocery.kind} == {MemoryKind.CALENDAR_EVENT, MemoryKind.GROCERY_ITEM}


def test_prompt_context_requires_explicit_space_and_surfaces_privacy(repo):
    private, _ = _people(repo)
    repo.admit_memory(
        "alice", space_id=private.space_id, kind=MemoryKind.ASSERTED_FACT,
        content={"preference": "quiet mornings"}, provenance="alice",
        governance=MemoryGovernance(allow_inference=True),
    )
    with pytest.raises(AmbiguousContext):
        repo.build_prompt_context("alice", space_ids=[], query="morning", purpose="answer")
    snapshot = repo.build_prompt_context("alice", space_ids=[private.space_id], query="morning", purpose="answer")
    assert snapshot["privacy"]["active_space_ids"] == [private.space_id]
    assert snapshot["privacy"]["disclosure_allowed"] is False
    assert snapshot["records"][0]["content"]["preference"] == "quiet mornings"


def test_prompt_context_enforces_record_purpose(repo):
    private, _ = _people(repo)
    repo.admit_memory(
        "alice", space_id=private.space_id, kind=MemoryKind.ASSERTED_FACT,
        content={"preference": "quiet mornings"}, provenance="alice",
        governance=MemoryGovernance(allow_inference=True, purposes=("schedule",)),
    )
    denied = repo.build_prompt_context(
        "alice", space_ids=[private.space_id], query="morning", purpose="shopping",
    )
    allowed = repo.build_prompt_context(
        "alice", space_ids=[private.space_id], query="morning", purpose="schedule",
    )
    assert denied["records"] == []
    assert len(allowed["records"]) == 1


def test_domain_filtering_precedes_ranking_and_cross_domain_access_is_explicit(repo):
    private, _ = _people(repo)
    for domain, value in (("health", "synthetic-medication"), ("financial", "synthetic-balance")):
        repo.admit_memory(
            "alice", space_id=private.space_id, kind=MemoryKind.ASSERTED_FACT,
            content={"value": value}, provenance=f"synthetic:{domain}",
            governance=MemoryGovernance(
                data_domains=(domain,), key_domain=domain,
                purposes=("planning",), allow_inference=True,
            ),
        )
    health = repo.retrieve_context("alice", MemoryRetrievalRequest(
        space_ids=(private.space_id,), data_domains=("health",),
        purpose="planning", query="synthetic",
    ))
    assert [record["content"]["value"] for record in health.records] == ["synthetic-medication"]
    assert health.remote_allowed is False
    assert health.citations == ("synthetic:health",)


def test_correction_deletion_and_member_revocation_invalidate_derived_views(repo):
    private, _ = _people(repo)
    source = repo.admit_memory(
        "alice", space_id=private.space_id, kind=MemoryKind.ASSERTED_FACT,
        content={"preference": "quiet"}, provenance="synthetic",
        governance=MemoryGovernance(data_domains=("core-private",), key_domain="core-private"),
    )
    descriptor = DerivedViewDescriptor(
        view_id="embedding-1", view_kind="embedding", source_record_id=source.record_id,
        source_revision=source.revision, space_id=source.space_id,
        data_domains=("core-private",), index_namespace="index:alice:core-private",
        algorithm=AlgorithmProvenance(algorithm_id="synthetic-embed", algorithm_version="1"),
    )
    repo.register_derived_view("alice", descriptor)
    repo.correct_memory("alice", source.record_id, {"preference": "calm"}, "person correction")
    receipts = repo.invalidation_receipts("alice", source.record_id)
    assert [(receipt.view_id, receipt.reason) for receipt in receipts] == [("embedding-1", "correction")]

    shared = repo.create_space("alice", household_id="household-one", name="Shared", purpose="coordinate")
    repo.invite_member("alice", shared.space_id, "bob", MemberRole.VIEWER)
    repo.accept_invitation("bob", shared.space_id)
    shared_source = repo.admit_memory(
        "alice", space_id=shared.space_id, kind=MemoryKind.ASSERTED_FACT,
        content={"maintenance": "synthetic"}, provenance="synthetic",
        governance=MemoryGovernance(data_domains=("household-shared",), key_domain="household-shared"),
    )
    repo.register_derived_view("alice", descriptor.model_copy(update={
        "view_id": "summary-1", "view_kind": "summary",
        "source_record_id": shared_source.record_id, "source_revision": 1,
        "space_id": shared.space_id, "data_domains": ("household-shared",),
        "index_namespace": "index:household:shared",
    }))
    repo.remove_member("alice", shared.space_id, "bob")
    assert repo.invalidation_receipts("alice", shared_source.record_id)[0].reason == "membership-revocation"


def test_usage_evidence_proposes_but_never_activates_without_person_approval(repo):
    candidate = DataDomainDefinition(
        domain_id="legal", display_name="Legal", description="Legal matters and records", origin="usage",
    )
    moments = (
        datetime(2026, 8, 10, tzinfo=timezone.utc),
        datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
        datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
    for index, observed_at in enumerate(moments):
        repo.observe_taxonomy_usage("alice", TaxonomyUsageSignal(
            signal_id=f"legal-{index}", candidate_domain_id="legal",
            current_domain_ids=("core-private",), signal_type="repeated-request",
            suggested_level="security-domain", observed_at=observed_at,
            source_reference=f"event-{index}",
        ))
    proposal = repo.evaluate_taxonomy_candidate("alice", candidate, "security-domain")
    assert proposal is not None
    assert proposal.evidence_count == 3
    assert proposal.requires_explicit_approval is True
    assert repo.list_taxonomy_domains("alice") == []
    assert repo.list_taxonomy_proposals("bob") == []

    repo.record_taxonomy_security_review("alice", TaxonomySecurityReview(
        review_id="review-existing-legal", proposal_id=proposal.proposal_id, decision="approve",
        policy_version="taxonomy-policy.v1", separate_key_boundary=True,
        retention_reviewed=True, sharing_reviewed=True, disclosure_reviewed=True,
        rationale="Synthetic full-boundary review.",
    ))
    receipt = repo.decide_taxonomy_proposal("alice", TaxonomyDecision(
        decision_id="decision-legal", proposal_id=proposal.proposal_id,
        decision="approve", explicit_confirmation=True, migration_scope="none",
    ))
    assert receipt is not None
    assert receipt.migration_status == "not-started"
    assert [item.domain_id for item in repo.list_taxonomy_domains("alice")] == ["legal"]


def test_taxonomy_threshold_and_decline_cooldown_prevent_prompt_fatigue(repo):
    candidate = DataDomainDefinition(
        domain_id="projects", display_name="Projects", description="Long-running projects", origin="usage",
    )
    for index, day in enumerate((10, 11)):
        repo.observe_taxonomy_usage("alice", TaxonomyUsageSignal(
            signal_id=f"project-{index}", candidate_domain_id="projects",
            signal_type="classification-correction", suggested_level="subdomain",
            observed_at=datetime(2026, 8, day, tzinfo=timezone.utc),
        ))
    assert repo.evaluate_taxonomy_candidate("alice", candidate, "subdomain") is None
    repo.observe_taxonomy_usage("alice", TaxonomyUsageSignal(
        signal_id="project-2", candidate_domain_id="projects",
        signal_type="classification-correction", suggested_level="subdomain",
        observed_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
    ))
    proposal = repo.evaluate_taxonomy_candidate("alice", candidate, "subdomain")
    repo.decide_taxonomy_proposal("alice", TaxonomyDecision(
        decision_id="decline-projects", proposal_id=proposal.proposal_id, decision="decline",
    ))
    assert repo.evaluate_taxonomy_candidate("alice", candidate, "subdomain") is None


def _security_proposal(repo):
    candidate = DataDomainDefinition(
        domain_id="legal", display_name="Legal", description="Legal matters", origin="usage",
    )
    for index, day in enumerate((10, 10, 11)):
        repo.observe_taxonomy_usage("alice", TaxonomyUsageSignal(
            signal_id=f"security-{index}", candidate_domain_id="legal",
            signal_type="policy-friction", suggested_level="security-domain",
            observed_at=datetime(2026, 8, day, index, tzinfo=timezone.utc),
        ))
    return repo.evaluate_taxonomy_candidate("alice", candidate, "security-domain")


def test_natural_preview_and_security_review_gate_activation(repo):
    proposal = _security_proposal(repo)
    preview = repo.taxonomy_proposal_preview("alice", proposal.proposal_id)
    assert "Would you like that?" in preview.prompt
    assert preview.requires_security_review is True
    assert any("No records move" in item for item in preview.would_not_change)
    decision = TaxonomyDecision(
        decision_id="approve-legal", proposal_id=proposal.proposal_id,
        decision="approve", explicit_confirmation=True,
    )
    with pytest.raises(ValueError, match="security review"):
        repo.decide_taxonomy_proposal("alice", decision)
    repo.record_taxonomy_security_review("alice", TaxonomySecurityReview(
        review_id="review-legal", proposal_id=proposal.proposal_id, decision="approve",
        policy_version="taxonomy-policy.v1", separate_key_boundary=True,
        retention_reviewed=True, sharing_reviewed=True, disclosure_reviewed=True,
        rationale="Separate legal material while preserving deny-by-default sharing.",
    ))
    assert repo.decide_taxonomy_proposal("alice", decision).domain.status == "active"


def test_migration_preview_confirmation_staleness_and_rollback(repo):
    private, _ = _people(repo)
    record = repo.admit_memory(
        "alice", space_id=private.space_id, kind=MemoryKind.ASSERTED_FACT,
        content={"synthetic": "document"}, provenance="synthetic",
        governance=MemoryGovernance(data_domains=("core-private",), key_domain="core-private"),
    )
    proposal = _security_proposal(repo)
    repo.record_taxonomy_security_review("alice", TaxonomySecurityReview(
        review_id="review-migration", proposal_id=proposal.proposal_id, decision="approve",
        policy_version="taxonomy-policy.v1", separate_key_boundary=True,
        retention_reviewed=True, sharing_reviewed=True, disclosure_reviewed=True,
        rationale="Approved controls.",
    ))
    repo.decide_taxonomy_proposal("alice", TaxonomyDecision(
        decision_id="approve-migration", proposal_id=proposal.proposal_id,
        decision="approve", explicit_confirmation=True,
    ))
    preview = repo.preview_taxonomy_migration(
        "alice", proposal.proposal_id, source_domain_ids=("core-private",),
        selected_record_ids=(record.record_id,),
    )
    assert preview.record_revisions == {record.record_id: 1}
    with pytest.raises(ValueError, match="expired or has changed"):
        repo.execute_taxonomy_migration("alice", TaxonomyMigrationCommand(
            preview_id=preview.preview_id, confirmation_digest="wrong",
            explicit_confirmation=True,
        ))
    receipt = repo.execute_taxonomy_migration("alice", TaxonomyMigrationCommand(
        preview_id=preview.preview_id, confirmation_digest=preview.confirmation_digest,
        explicit_confirmation=True,
    ))
    migrated = repo.get_memory("alice", record.record_id)
    assert migrated.governance.key_domain == "legal"
    assert "legal" in migrated.governance.data_domains
    rollback = repo.rollback_taxonomy_migration("alice", receipt.migration_id)
    assert rollback.restored_record_ids == (record.record_id,)
    restored = repo.get_memory("alice", record.record_id)
    assert restored.governance.key_domain == "core-private"
    assert restored.governance.data_domains == ("core-private",)
