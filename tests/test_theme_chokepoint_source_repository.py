from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import sqlite3

import pytest

from event_collector.theme_chokepoint.contracts import (
    DemandFrame,
    ProductAnchor,
    ResearchRequest,
    RunStatus,
    SourceSnapshot,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.source_identity import (
    CanonicalSourceIdentityResolver,
    SourceIdentity,
    SourceProvenance,
    SourceRelation,
    SourceResolutionRawResponse,
    normalized_quote_sha256,
    normalized_source_content_sha256,
    resolve_source_identity,
)


RETRIEVED_AT = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)
PUBLICATION_TIME = datetime(2026, 8, 16, 9, 30, tzinfo=timezone.utc)
SOURCE_TEXT = "Manufacturer confirmed a new capacity expansion in Arizona."
EXACT_QUOTE = "confirmed a new capacity expansion"


class _ResolutionClient:
    def __init__(self, *, relation=SourceRelation.ORIGINAL, origin_url=None):
        self.relation = relation
        self.origin_url = origin_url

    def resolve(self, **kwargs):
        return SourceResolutionRawResponse(
            provider="controlled-origin-resolver",
            provider_trace_id="resolver-" + sha256(
                kwargs["source_url"].encode("utf-8")
            ).hexdigest()[:16],
            http_status=200,
            raw_body=json.dumps(
                {
                    "observed_url": kwargs["source_url"],
                    "terminal_url": kwargs["source_url"],
                    "redirect_chain": [],
                    "relation": self.relation.value,
                    "origin_url": self.origin_url,
                    "resolution_status": "verified",
                },
                sort_keys=True,
            ).encode("utf-8"),
            retrieved_at=RETRIEVED_AT,
        )


def _verified_identity(source_url, *, relation=SourceRelation.ORIGINAL, origin_url=None):
    return CanonicalSourceIdentityResolver(
        _ResolutionClient(relation=relation, origin_url=origin_url)
    ).resolve(source_url)


def _save_identity(repository, identity, *, origin_event_id="event:capacity-expansion"):
    return repository.save_source_identity(
        identity,
        canonical_publisher_id="publisher:manufacturer",
        canonical_document_id="document:capacity-release",
        origin_event_id=origin_event_id,
    )


def _save_version(
    repository,
    source_identity_id,
    *,
    raw_bytes=SOURCE_TEXT.encode("utf-8"),
    normalized_content=SOURCE_TEXT,
    exact_quote=EXACT_QUOTE,
):
    quote_start = normalized_content.index(exact_quote)
    return repository.save_source_version(
        source_identity_id=source_identity_id,
        retrieved_at=RETRIEVED_AT,
        raw_bytes=raw_bytes,
        normalized_content=normalized_content,
        quote_span=(quote_start, quote_start + len(exact_quote), exact_quote),
        content_type="text/html",
        language="en",
        publication_time=PUBLICATION_TIME,
        updated_time=None,
    )


def _prepare_run_for_stage3(repository):
    request = ResearchRequest(
        run_id="source-lineage-run",
        theme="AI capacity",
        trigger="capacity update",
        region="global",
        as_of_date=date(2026, 8, 17),
        time_horizon_months=24,
        analysis_goal="source lineage",
        seed_products=(),
        seed_companies=(),
        research_mode="assisted",
        max_depth=1,
        max_nodes=1,
        max_iterations=1,
        max_sources=2,
        max_time_seconds=60,
        max_cost_usd=1.0,
        max_product_anchors=1,
    )
    repository.create_request(request)
    repository.save_framing(
        request.run_id,
        DemandFrame(
            normalized_theme=request.theme,
            scope="global",
            exclusions=(),
            demand_hypothesis="Capacity demand is increasing.",
            measurable_demand_variables=("capacity",),
            time_horizon_months=24,
            unresolved_questions=(),
        ),
        status=RunStatus.AWAITING_PRODUCT_CONFIRMATION,
        anchors=(
            ProductAnchor(
                anchor_id="source-lineage-anchor",
                product_name="AI capacity",
                buyer_or_user="operator",
                demand_variable="capacity",
                theme_link="Capacity supports AI demand.",
                confidence=0.9,
                supporting_evidence_ids=(),
                missing_evidence=(),
                status="proposed",
            ),
        ),
    )
    repository.confirm_product_anchors(
        request.run_id,
        ("source-lineage-anchor",),
        confirmed_by="owner",
    )
    repository.save_supply_chain_graph(
        request.run_id,
        nodes=(
            SupplyChainNode(
                node_id="source-lineage-node",
                normalized_name="ai capacity",
                node_type="product_anchor",
                depth=0,
                status="confirmed",
                description="Confirmed source-lineage test node.",
                aliases=(),
                product_anchor_id="source-lineage-anchor",
            ),
        ),
        edges=(),
        truncation_reasons=(),
    )
    return request


def test_source_identity_and_version_are_immutable_and_reloadable(tmp_path):
    """SELECT INVARIANT: source authority requires immutable repository reload."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    identity = resolve_source_identity(
        "https://manufacturer.example/releases/capacity?utm_source=feed"
    )

    stored_identity = _save_identity(repository, identity)
    stored_version = _save_version(repository, stored_identity.source_identity_id)

    assert (
        repository.get_source_identity(stored_identity.source_identity_id)
        == stored_identity
    )
    assert (
        repository.get_source_version(stored_version.source_version_id)
        == stored_version
    )
    with pytest.raises(FrozenInstanceError):
        stored_identity.provenance = SourceProvenance.UNKNOWN
    with pytest.raises(FrozenInstanceError):
        stored_version.version_sequence = 99


def test_source_version_sequence_and_content_lineage_are_repository_bound(tmp_path):
    """SELECT INVARIANT: every immutable version binds bytes, text, and quote span."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    identity = _save_identity(
        repository,
        resolve_source_identity("https://manufacturer.example/releases/capacity"),
    )

    first = _save_version(repository, identity.source_identity_id)
    revised_text = SOURCE_TEXT + " Construction starts next quarter."
    second = _save_version(
        repository,
        identity.source_identity_id,
        raw_bytes=revised_text.encode("utf-8"),
        normalized_content=revised_text,
    )

    assert (first.version_sequence, second.version_sequence) == (1, 2)
    assert first.source_version_id != second.source_version_id
    assert first.raw_bytes_sha256 == sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
    assert first.normalized_content_sha256 == normalized_source_content_sha256(
        SOURCE_TEXT
    )
    assert first.quote_span_sha256 == normalized_quote_sha256(EXACT_QUOTE)
    assert first.source_identity_id == second.source_identity_id


@pytest.mark.parametrize(
    ("identity", "expected_authorized"),
    (
        (
            _verified_identity("https://manufacturer.example/releases/original"),
            True,
        ),
        (
            resolve_source_identity(
                "https://short.example/capacity",
                redirect_chain=("https://manufacturer.example/releases/original",),
            ),
            False,
        ),
        (
            _verified_identity(
                "https://wire.example/reprint",
                relation=SourceRelation.REPRINT,
                origin_url="https://manufacturer.example/releases/original",
            ),
            False,
        ),
        (
            resolve_source_identity(
                "https://news.example/ambiguous",
                provenance=SourceProvenance.AMBIGUOUS,
            ),
            False,
        ),
        (
            resolve_source_identity(
                "https://news.example/unknown",
                provenance=SourceProvenance.UNKNOWN,
            ),
            False,
        ),
    ),
)
def test_persisted_relation_and_provenance_gate_independent_events(
    tmp_path, identity, expected_authorized
):
    """SELECT INVARIANT: only a persisted verified original can authorize an event."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    stored_identity = _save_identity(repository, identity)
    version = _save_version(repository, stored_identity.source_identity_id)
    reloaded = repository.get_source_identity(stored_identity.source_identity_id)

    assert reloaded.relation is identity.relation
    assert reloaded.provenance is identity.provenance
    assert repository.authorizes_independent_industry_event(
        version.source_version_id
    ) is expected_authorized


def test_same_version_seen_through_an_alias_cannot_duplicate_an_event(tmp_path):
    """SELECT INVARIANT: a raw/version alias resolves to one industry event."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    original = _save_identity(
        repository,
        _verified_identity("https://manufacturer.example/releases/original"),
    )
    alias = _save_identity(
        repository,
        _verified_identity(
            "https://wire.example/alias",
            relation=SourceRelation.ALIAS,
            origin_url="https://manufacturer.example/releases/original",
        ),
    )
    original_version = _save_version(repository, original.source_identity_id)
    alias_version = _save_version(repository, alias.source_identity_id)

    assert repository.authorizes_independent_industry_event(
        original_version.source_version_id
    ) is True
    assert repository.authorizes_independent_industry_event(
        alias_version.source_version_id
    ) is False
    assert repository.resolve_industry_event_identity(
        alias_version.source_version_id
    ) == repository.resolve_industry_event_identity(
        original_version.source_version_id
    )


def test_repository_rejects_caller_constructed_verified_identity_without_binding(
    tmp_path,
):
    """SELECT INVARIANT: stored VERIFIED alone is not provenance authority."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    identity = SourceIdentity(
        canonical_url="https://manufacturer.example/releases/unbound",
        redirect_chain=("https://manufacturer.example/releases/unbound",),
        relation=SourceRelation.ORIGINAL,
        provenance=SourceProvenance.VERIFIED,
        origin_canonical_url=None,
    )
    with pytest.raises(ValueError, match="controlled source resolver execution"):
        _save_identity(repository, identity)


def test_repository_rejects_exact_looking_caller_forged_resolver_binding(tmp_path):
    """SELECT INVARIANT: copied receipt fields are not resolver execution authority."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    resolved = _verified_identity(
        "https://manufacturer.example/releases/forged-receipt"
    )
    forged = SourceIdentity(
        canonical_url=resolved.canonical_url,
        redirect_chain=resolved.redirect_chain,
        relation=resolved.relation,
        provenance=resolved.provenance,
        origin_canonical_url=resolved.origin_canonical_url,
        policy_id=resolved.policy_id,
        policy_sha256=resolved.policy_sha256,
        governance_bundle_id=resolved.governance_bundle_id,
        governance_bundle_sha256=resolved.governance_bundle_sha256,
        resolver_receipt_id=resolved.resolver_receipt_id,
        resolver_receipt_sha256=resolved.resolver_receipt_sha256,
    )

    with pytest.raises(ValueError, match="controlled source resolver execution"):
        _save_identity(repository, forged)


def test_industry_event_authority_reloads_persisted_resolver_raw_execution(tmp_path):
    """SELECT INVARIANT: resolver receipt fields require durable raw execution bytes."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    stored = _save_identity(
        repository,
        _verified_identity("https://manufacturer.example/releases/durable-resolver"),
    )
    version = _save_version(repository, stored.source_identity_id)

    with sqlite3.connect(repository.db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM theme_chokepoint_source_resolution_receipts"
        ).fetchone()[0]
        connection.execute(
            """UPDATE theme_chokepoint_source_resolution_receipts
               SET raw_body = ? WHERE receipt_id = ?""",
            (b'{"forged":true}', stored.resolver_receipt_id),
        )

    assert count == 1
    assert repository.authorizes_independent_industry_event(
        version.source_version_id
    ) is False


def test_repository_reloads_controlled_resolver_policy_binding(tmp_path):
    """SELECT INVARIANT: monitoring gate reloads exact resolver governance."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    identity = _verified_identity(
        "https://manufacturer.example/releases/policy-bound"
    )
    stored = _save_identity(repository, identity)
    version = _save_version(repository, stored.source_identity_id)

    reloaded = repository.get_source_identity(stored.source_identity_id)
    assert reloaded.policy_id == identity.policy_id
    assert reloaded.policy_sha256 == identity.policy_sha256
    assert reloaded.resolver_receipt_id == identity.resolver_receipt_id
    assert repository.authorizes_independent_industry_event(
        version.source_version_id
    ) is True


def test_stage_save_paths_materialize_legacy_snapshots_with_unknown_usage_lineage(
    tmp_path,
):
    """SELECT INVARIANT: Stage saves materialize every snapshot fail-closed."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _prepare_run_for_stage3(repository)
    stage3_snapshot = SourceSnapshot(
        article_id="legacy-stage3-source",
        canonical_url="https://industry.example/releases/legacy-update",
        content_hash=sha256(b"Legacy industry update.").hexdigest(),
        original_text="Legacy industry update.",
    )
    stage3 = repository.save_stage3_result(
        request.run_id,
        status=RunStatus.CHOKEPOINT_ASSESSMENT_READY,
        contract_version="theme-chokepoint-scoring-v1.4",
        executable_contract_id="source-lineage-contract",
        executable_contract_sha256="a" * 64,
        claims=(),
        evidence_cards=(),
        source_snapshots=(stage3_snapshot,),
        assessments=(),
        iterations_completed=1,
        incomplete_reasons=(),
    )
    stage4_snapshot = SourceSnapshot(
        article_id="legacy-stage4-source",
        canonical_url="https://company.example/releases/legacy-update",
        content_hash=sha256(b"Legacy company update.").hexdigest(),
        original_text="Legacy company update.",
    )
    repository.save_stage4_result(
        request.run_id,
        contract_version=stage3.contract_version,
        executable_contract_id=stage3.executable_contract_id,
        executable_contract_sha256=stage3.executable_contract_sha256,
        challenger_sets=(),
        company_assessments=(),
        company_source_snapshots=(stage4_snapshot,),
    )

    for stage, snapshots in (
        ("stage3", stage3.source_snapshots),
        ("stage4", (stage4_snapshot,)),
    ):
        usages = repository.list_source_usages(request.run_id, stage=stage)
        assert len(usages) == len(snapshots)
        assert {usage.source_snapshot_article_id for usage in usages} == {
            snapshot.article_id for snapshot in snapshots
        }
        for usage in usages:
            assert usage.run_id == request.run_id
            assert usage.stage == stage
            identity = repository.get_source_identity(usage.source_identity_id)
            version = repository.get_source_version(usage.source_version_id)
            assert version.source_identity_id == identity.source_identity_id
            assert identity.provenance is SourceProvenance.UNKNOWN
            assert repository.authorizes_independent_industry_event(
                version.source_version_id
            ) is False
