from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import sqlite3
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.contracts import (
    AnchorConditionResult,
    BoundBasis,
    CanonicalScoringRawProviderResponse,
    Claim,
    DemandFrame,
    DimensionRatingDraft,
    EvidenceCard,
    HotspotDraft,
    HotspotEvent,
    MonitoringEvidence,
    MonitoringEvaluatorRawResponse,
    MonitoringTriggerDraft,
    ObjectRefreshDraft,
    ProductAnchor,
    ResearchRequest,
    RunStatus,
    SegmentAssessment,
    SourceSnapshot,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.relief import (
    ReliefHorizonService,
    ReliefScenarioInput,
)
from event_collector.theme_chokepoint.stage5 import PersistentResearchProductService
from event_collector.theme_chokepoint.source_identity import (
    CanonicalSourceIdentityResolver,
    SourceProvenance,
    SourceResolutionRawResponse,
    resolve_source_identity,
)
from event_collector.theme_chokepoint.stage6 import (
    CANONICAL_GOVERNANCE_BUNDLE_ID,
    CANONICAL_GOVERNANCE_BUNDLE_SHA256,
    CanonicalAssessmentResult,
    CanonicalMonitoringRecomputer,
    HotspotMonitoringService,
    PersistedAssessmentSnapshot,
    Stage34CanonicalCalculator,
    _assert_persisted_before_snapshot,
    _derive_trend,
)
from test_theme_chokepoint_stage5 import _ready_stage4


NOW = datetime(2026, 8, 16, 12, tzinfo=timezone.utc)


class _VerifiedOriginalResolverClient:
    def resolve(self, **kwargs):
        return SourceResolutionRawResponse(
            provider="controlled-origin-resolver",
            provider_trace_id="stage6-source-resolution-1",
            http_status=200,
            raw_body=json.dumps(
                {
                    "observed_url": kwargs["source_url"],
                    "terminal_url": kwargs["source_url"],
                    "redirect_chain": [],
                    "relation": "original",
                    "origin_url": None,
                    "resolution_status": "verified",
                },
                sort_keys=True,
            ).encode("utf-8"),
            retrieved_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        )


def _minimal_canonical_segment_target(repository):
    request = ResearchRequest(
        run_id="canonical-stage34-run",
        theme="AI capacity",
        trigger="capacity update",
        region="global",
        as_of_date=date(2026, 8, 17),
        time_horizon_months=24,
        analysis_goal="canonical monitoring",
        seed_products=(),
        seed_companies=(),
        research_mode="assisted",
        max_depth=1,
        max_nodes=1,
        max_iterations=1,
        max_sources=1,
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
                anchor_id="canonical-stage34-anchor",
                product_name="AI capacity",
                buyer_or_user="operator",
                demand_variable="capacity",
                theme_link="Capacity supports demand.",
                confidence=0.9,
                supporting_evidence_ids=(),
                missing_evidence=(),
                status="proposed",
            ),
        ),
    )
    repository.confirm_product_anchors(
        request.run_id,
        ("canonical-stage34-anchor",),
        confirmed_by="owner",
    )
    repository.save_supply_chain_graph(
        request.run_id,
        nodes=(
            SupplyChainNode(
                node_id="canonical-stage34-segment",
                normalized_name="ai capacity",
                node_type="product_anchor",
                depth=0,
                status="confirmed",
                description="Canonical calculator target.",
                aliases=(),
                product_anchor_id="canonical-stage34-anchor",
            ),
        ),
        edges=(),
        truncation_reasons=(),
    )
    assessment = SegmentAssessment(
        segment_id="canonical-stage34-segment",
        contract_version="theme-chokepoint-scoring-v1.4",
        dimensions=(),
        score_min=50,
        score_max=60,
        presence_coverage=1,
        resolved_coverage=1,
        decision_coverage=1,
        conflicted_weight_share=0,
        unknown_weight_share=0,
        stale_weight_share=0,
        primary_state="watch_segment",
        missing_material_fields=(),
        relief_horizon=None,
        eligible=True,
        achieved_gates=("watch_gate",),
    )
    repository.save_stage3_result(
        request.run_id,
        status=RunStatus.CHOKEPOINT_ASSESSMENT_READY,
        contract_version=assessment.contract_version,
        executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
        executable_contract_sha256=(
            "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
        ),
        claims=(),
        evidence_cards=(),
        source_snapshots=(),
        assessments=(assessment,),
        iterations_completed=1,
        incomplete_reasons=(),
    )
    repository.save_stage4_result(
        request.run_id,
        contract_version=assessment.contract_version,
        executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
        executable_contract_sha256=(
            "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
        ),
        challenger_sets=(),
        company_assessments=(),
    )
    return request, assessment


def _raw_monitoring_result(result, trace_id):
    payload = asdict(result)
    payload.pop("recompute_receipt_id", None)
    payload.pop("relief_assessment", None)
    return MonitoringEvaluatorRawResponse(
        provider="controlled-monitor-model",
        provider_trace_id=trace_id,
        http_status=200,
        raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        retrieved_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        cost_usd=0.01,
    )


def _raw_monitoring_observation(
    *, target_type, target_id, changed_claim_ids, changed_dimensions, explanation, trace_id
):
    return MonitoringEvaluatorRawResponse(
        provider="controlled-monitor-model",
        provider_trace_id=trace_id,
        http_status=200,
        raw_body=json.dumps(
            {
                "target_type": target_type,
                "target_id": target_id,
                "changed_claim_ids": list(changed_claim_ids),
                "changed_dimensions": list(changed_dimensions),
                "explanation": explanation,
            },
            sort_keys=True,
        ).encode("utf-8"),
        retrieved_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        cost_usd=0.01,
    )


def _verified_segment_monitoring_evidence(repository, assessment):
    original_text = "Verified new capacity evidence."
    exact_quote = "Verified new capacity evidence"
    content_hash = "sha256:" + sha256(original_text.encode("utf-8")).hexdigest()
    claim = Claim(
        claim_id="canonical-new-claim",
        node_id=assessment.segment_id,
        claim_type="source_fact",
        statement=exact_quote,
        material_field="supply_concentration",
        primary_scoring_dimension="supply_concentration",
        scoring_use="primary",
        evidence_ids=("canonical-new-evidence",),
    )
    card = EvidenceCard(
        evidence_id="canonical-new-evidence",
        claim_id=claim.claim_id,
        article_id="canonical-new-article",
        canonical_url="https://verified.example/new-capacity",
        source_title="Verified capacity release",
        publisher="Verified Publisher",
        source_type="official_release",
        publication_date=date(2026, 8, 18),
        data_as_of_date=date(2026, 8, 18),
        location="body",
        quote_start=0,
        quote_end=len(exact_quote),
        exact_quote=exact_quote,
        content_hash=content_hash,
        stance="supports",
        limitations="single verified event",
        extraction_model="canonical-fixture",
        prompt_version="canonical-fixture-v1",
        origin_event_id="canonical-new-event",
        evidence_family_id="canonical-new-family",
    )
    snapshot = SourceSnapshot(
        article_id=card.article_id,
        canonical_url=card.canonical_url,
        content_hash=content_hash,
        original_text=original_text,
    )
    identity = repository.save_source_identity(
        CanonicalSourceIdentityResolver(
            _VerifiedOriginalResolverClient()
        ).resolve(snapshot.canonical_url),
        canonical_publisher_id="publisher:verified",
        canonical_document_id=snapshot.article_id,
        origin_event_id=card.origin_event_id,
    )
    version = repository.save_source_version(
        source_identity_id=identity.source_identity_id,
        retrieved_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        raw_bytes=original_text.encode("utf-8"),
        normalized_content=original_text,
        quote_span=(0, len(exact_quote), exact_quote),
        content_type="text/plain",
        language="en",
        publication_time=datetime(2026, 8, 18, tzinfo=timezone.utc),
        updated_time=None,
    )
    return MonitoringEvidence(
        event_id="canonical-new-monitoring-event",
        event_type="capacity_update",
        target_type="segment_assessment",
        target_id=assessment.segment_id,
        evidence_ids=(card.evidence_id,),
        event_time=datetime(2026, 8, 18, 8, tzinfo=timezone.utc),
        published_at=datetime(2026, 8, 18, 9, tzinfo=timezone.utc),
        assessment_as_of=datetime(2026, 8, 18, 10, tzinfo=timezone.utc),
        new_claims=(claim,),
        new_evidence_cards=(card,),
        new_source_snapshots=(snapshot,),
        source_version_ids=(version.source_version_id,),
        governance_bundle_sha256=CANONICAL_GOVERNANCE_BUNDLE_SHA256,
    )


def _verified_monitoring_evidence(
    repository,
    *,
    target_type,
    target_id,
    claim_id="new-claim",
    dimension="technical_performance_gap",
    event_id="verified-monitoring-event",
):
    text = f"Verified evidence for {event_id}."
    exact_quote = text.removesuffix(".")
    article_id = f"article-{event_id}"
    evidence_id = f"evidence-{event_id}"
    canonical_url = f"https://verified.example/{event_id}"
    content_hash = "sha256:" + sha256(text.encode("utf-8")).hexdigest()
    claim = Claim(
        claim_id=claim_id,
        node_id=target_id,
        claim_type="source_fact",
        statement=exact_quote,
        material_field=dimension,
        primary_scoring_dimension=dimension,
        scoring_use="primary",
        evidence_ids=(evidence_id,),
    )
    card = EvidenceCard(
        evidence_id=evidence_id,
        claim_id=claim_id,
        article_id=article_id,
        canonical_url=canonical_url,
        source_title="Verified monitoring release",
        publisher="Verified Publisher",
        source_type="official_release",
        publication_date=date(2026, 8, 18),
        data_as_of_date=date(2026, 8, 18),
        location="body",
        quote_start=0,
        quote_end=len(exact_quote),
        exact_quote=exact_quote,
        content_hash=content_hash,
        stance="supports",
        limitations="single verified event",
        extraction_model="canonical-fixture",
        prompt_version="canonical-fixture-v1",
        origin_event_id=f"origin-{event_id}",
        evidence_family_id=f"family-{event_id}",
    )
    snapshot = SourceSnapshot(
        article_id=article_id,
        canonical_url=canonical_url,
        content_hash=content_hash,
        original_text=text,
    )
    identity = repository.save_source_identity(
        CanonicalSourceIdentityResolver(
            _VerifiedOriginalResolverClient()
        ).resolve(canonical_url),
        canonical_publisher_id="publisher:verified",
        canonical_document_id=article_id,
        origin_event_id=card.origin_event_id,
    )
    version = repository.save_source_version(
        source_identity_id=identity.source_identity_id,
        retrieved_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        raw_bytes=text.encode("utf-8"),
        normalized_content=text,
        quote_span=(0, len(exact_quote), exact_quote),
        content_type="text/plain",
        language="en",
        publication_time=datetime(2026, 8, 18, tzinfo=timezone.utc),
        updated_time=None,
    )
    return MonitoringEvidence(
        event_id=event_id,
        event_type="verified_update",
        target_type=target_type,
        target_id=target_id,
        evidence_ids=(evidence_id,),
        event_time=datetime(2026, 8, 18, 8, tzinfo=timezone.utc),
        published_at=datetime(2026, 8, 18, 9, tzinfo=timezone.utc),
        assessment_as_of=datetime(2026, 8, 18, 10, tzinfo=timezone.utc),
        new_claims=(claim,),
        new_evidence_cards=(card,),
        new_source_snapshots=(snapshot,),
        source_version_ids=(version.source_version_id,),
        governance_bundle_sha256=CANONICAL_GOVERNANCE_BUNDLE_SHA256,
    )


def _canonical_result_from_draft(draft):
    return CanonicalAssessmentResult(
        target_type=draft.target_type,
        target_id=draft.target_id,
        after_state=draft.after_state,
        after_score_min=draft.after_score_min,
        after_score_max=draft.after_score_max,
        after_mandatory_gate_level=draft.after_mandatory_gate_level,
        after_mandatory_floor_valid=draft.after_mandatory_floor_valid,
        changed_claim_ids=draft.changed_claim_ids,
        changed_dimensions=draft.changed_dimensions,
        explanation=draft.explanation,
        recompute_receipt_id=draft.recompute_receipt_id,
        executable_contract_id=draft.executable_contract_id,
        executable_contract_sha256=draft.executable_contract_sha256,
        original_constraint_active=draft.original_constraint_active,
        former_constraint_easing=draft.former_constraint_easing,
        adjacent_constraint_strengthening=draft.adjacent_constraint_strengthening,
        underlying_demand_intact=draft.underlying_demand_intact,
        relief_assessment=draft.relief_assessment,
    )


class FixedCanonicalScorer:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def score(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FixedRawCanonicalScorer:
    def __init__(self, result, trace_id):
        self.result = result
        self.trace_id = trace_id
        self.calls = []

    def score(self, **kwargs):
        self.calls.append(kwargs)
        payload = asdict(self.result)
        payload.pop("recompute_receipt_id", None)
        payload.pop("relief_assessment", None)
        return CanonicalScoringRawProviderResponse(
            provider="controlled-canonical-scorer",
            provider_trace_id=self.trace_id,
            http_status=200,
            raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
            retrieved_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
            cost_usd=0.01,
        )


class FixedClusterer:
    def cluster(self, events):
        return [
            HotspotDraft(
                theme="AI data-center UPS lead times",
                window="7d",
                volume_acceleration=0.9,
                source_diversity=0.8,
                novelty=0.7,
                company_breadth=0.6,
                sector_breadth=0.5,
                primary_source_confirmation=1.0,
                persistence=0.8,
                trigger_event_ids=tuple(event.event_id for event in events),
            )
        ]


class FixedRefresher:
    def __init__(self, draft):
        self.draft = draft
        self.calls = []

    def refresh(self, *, base_run_id, target_type, target_id, evidence):
        self.calls.append((base_run_id, target_type, target_id, tuple(evidence)))
        return self.draft


def _refresh_draft(
    *,
    target_id="assessment-1",
    before_state="before",
    after_state="after",
    changed_claim_ids=("claim-1",),
    changed_dimensions=("dimension-1",),
    change_type="industry_event",
    explanation="Comparable-scope evidence changed.",
    before_score_min=60,
    after_score_min=60,
    before_score_max=80,
    after_score_max=80,
    before_mandatory_gate_level=2,
    after_mandatory_gate_level=2,
    before_mandatory_floor_valid=True,
    after_mandatory_floor_valid=True,
    original_constraint_active=False,
    former_constraint_easing=False,
    adjacent_constraint_strengthening=False,
    underlying_demand_intact=True,
    relief_assessment=None,
):
    return ObjectRefreshDraft(
        target_type="company_assessment",
        target_id=target_id,
        before_state=before_state,
        after_state=after_state,
        changed_claim_ids=changed_claim_ids,
        changed_dimensions=changed_dimensions,
        change_type=change_type,
        explanation=explanation,
        before_score_min=before_score_min,
        after_score_min=after_score_min,
        before_score_max=before_score_max,
        after_score_max=after_score_max,
        before_mandatory_gate_level=before_mandatory_gate_level,
        after_mandatory_gate_level=after_mandatory_gate_level,
        before_mandatory_floor_valid=before_mandatory_floor_valid,
        after_mandatory_floor_valid=after_mandatory_floor_valid,
        original_constraint_active=original_constraint_active,
        former_constraint_easing=former_constraint_easing,
        adjacent_constraint_strengthening=adjacent_constraint_strengthening,
        underlying_demand_intact=underlying_demand_intact,
        relief_assessment=relief_assessment,
    )


def test_canonical_monitoring_requires_distinct_evaluator_and_scorer_identities(tmp_path):
    """SELECT INVARIANT: one producer cannot impersonate observation and state authority."""

    class DualRoleProducer:
        def recompute(self, **_kwargs):
            raise AssertionError("not reached")

        def score(self, **_kwargs):
            raise AssertionError("not reached")

    producer = DualRoleProducer()
    with pytest.raises(ValueError, match="distinct execution identities"):
        CanonicalMonitoringRecomputer(
            ThemeChokepointRepository(tmp_path / "theme.db"),
            producer,
            producer,
        )


def test_canonical_calculator_cannot_wrap_the_monitoring_evaluator(tmp_path):
    """SELECT INVARIANT: a concrete wrapper cannot hide one dual-role producer."""
    class DualRoleProducer:
        def recompute(self, **_kwargs):
            raise AssertionError("constructor must reject before execution")

        def score(self, **_kwargs):
            raise AssertionError("constructor must reject before execution")

    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    producer = DualRoleProducer()

    with pytest.raises(ValueError, match="distinct execution identities"):
        CanonicalMonitoringRecomputer(
            repository,
            producer,
            Stage34CanonicalCalculator(repository, producer),
        )


def test_canonical_monitoring_rejects_shared_provider_role_identity(tmp_path):
    """SELECT INVARIANT: separate adapters cannot share one provider role identity."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    base = company.defensibility

    evaluator = SimpleNamespace(
        recompute=lambda **_kwargs: _raw_monitoring_observation(
            target_type="company_assessment",
            target_id=company.assessment_id,
            changed_claim_ids=("new-claim",),
            changed_dimensions=("technical_performance_gap",),
            explanation="Evaluator observed a changed fact candidate.",
            trace_id="shared-role-evaluator",
        )
    )
    scorer_result = CanonicalAssessmentResult(
        target_type="company_assessment",
        target_id=company.assessment_id,
        after_state=base.primary_state,
        after_score_min=base.score_min,
        after_score_max=base.score_max,
        after_mandatory_gate_level=len(base.achieved_gates),
        after_mandatory_floor_valid=True,
        changed_claim_ids=("new-claim",),
        changed_dimensions=("technical_performance_gap",),
        explanation="Canonical scorer result.",
        recompute_receipt_id="ignored-local-receipt",
        executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
        executable_contract_sha256=(
            "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
        ),
    )

    class SharedProviderScorer(FixedRawCanonicalScorer):
        def score(self, **kwargs):
            return replace(super().score(**kwargs), provider="controlled-monitor-model")

    evidence = _verified_monitoring_evidence(
        repository,
        target_type="company_assessment",
        target_id=company.assessment_id,
        event_id="shared-provider-role-event",
    )
    with pytest.raises(ValueError, match="distinct provider role identities"):
        CanonicalMonitoringRecomputer(
            repository,
            evaluator,
            Stage34CanonicalCalculator(
                repository,
                SharedProviderScorer(scorer_result, "shared-role-scorer"),
            ),
        ).refresh(
            base_run_id=request.run_id,
            target_type="company_assessment",
            target_id=company.assessment_id,
            evidence=(evidence,),
        )


def _event():
    return HotspotEvent(
        event_id="event-hot-1",
        occurred_at=NOW,
        source_id="source-1",
        primary_source=True,
        companies=("PowerCo", "ChallengerCo"),
        sectors=("electrical equipment",),
        text="UPS lead times increased after new AI campus orders.",
    )


def test_stage6_hotspot_priority_preserves_inputs_and_human_decision(tmp_path):
    """SELECT INVARIANT: hotspot priority is inspectable and requires accept/reject."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    service = HotspotMonitoringService(repository, FixedClusterer(), FixedRefresher(None))

    candidates = service.propose_hotspots([_event()])
    accepted = service.decide_hotspot(
        candidates[0].hotspot_id, decision="accepted", actor="product-owner"
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.status == "proposed"
    assert candidate.score_purpose == "research_priority_only"
    assert candidate.trigger_event_ids == ("event-hot-1",)
    assert candidate.scoring_inputs["volume_acceleration"] == 0.9
    assert 0 <= candidate.priority_score <= 100
    assert accepted.status == "accepted"
    assert accepted.decided_by == "product-owner"
    assert repository.list_hotspots() == (accepted,)


def test_stage6_refreshes_only_triggered_object_and_explains_dimension_change(tmp_path):
    """SELECT INVARIANT: monitoring creates a delta; it never overwrites the frozen run."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    before = repository.get_stage4_result(request.run_id)
    company = before.company_assessments[0]
    draft = _refresh_draft(
        target_id=company.assessment_id,
        before_state=company.competition_primary_state,
        after_state="contested_chokepoint_owner",
        changed_claim_ids=("claim-powerco-customer_sourcing_evidence",),
        changed_dimensions=("customer_sourcing_evidence",),
        explanation="A named customer qualified a second source.",
        before_score_max=80,
        after_score_max=70,
    )
    refresher = FixedRefresher(draft)
    service = HotspotMonitoringService(repository, FixedClusterer(), refresher)
    trigger = service.add_monitoring_trigger(
        request.run_id,
        MonitoringTriggerDraft(
            target_type="company_assessment",
            target_id=company.assessment_id,
            event_types=("customer_qualification",),
            created_by="analyst",
        ),
    )
    evidence = MonitoringEvidence(
        event_id="monitor-event-1",
        event_type="customer_qualification",
        target_type="company_assessment",
        target_id=company.assessment_id,
        evidence_ids=("ev-powerco-customer_sourcing_evidence",),
        event_time=datetime(2026, 8, 17, 8, tzinfo=timezone.utc),
        published_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        assessment_as_of=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )

    result = service.refresh(request.run_id, [evidence])

    assert result.status is RunStatus.MONITORING_READY
    assert result.trigger_ids == (trigger.trigger_id,)
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.trend_state == "assessment_revised"
    assert change.changed_claim_ids == ("claim-powerco-customer_sourcing_evidence",)
    assert change.changed_dimensions == ("customer_sourcing_evidence",)
    assert change.responsible_evidence_ids == (
        "ev-powerco-customer_sourcing_evidence",
    )
    assert repository.get_stage4_result(request.run_id) == before


def test_stage6_persists_new_original_claim_and_evidence_before_refresh(tmp_path):
    """SELECT INVARIANT: monitoring accepts new source records through its own ledger."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    stage3 = repository.get_stage3_result(request.run_id)
    old_claim = stage3.claims[0]
    old_card = stage3.evidence_cards[0]
    old_snapshot = stage3.source_snapshots[0]
    text = "A newly published customer filing qualified a second supplier."
    content_hash = "sha256:" + sha256(text.encode("utf-8")).hexdigest()
    claim = replace(
        old_claim,
        claim_id="monitor-claim-new",
        evidence_ids=("monitor-evidence-new",),
        statement=text,
        fact_key="monitor-new-customer-qualification",
    )
    card = replace(
        old_card,
        evidence_id="monitor-evidence-new",
        claim_id=claim.claim_id,
        article_id="monitor-article-new",
        canonical_url="https://example.com/monitor/new-customer-qualification",
        content_hash=content_hash,
        exact_quote=text,
        quote_start=0,
        quote_end=len(text),
        business_fact_assertions=(),
    )
    snapshot = replace(
        old_snapshot,
        article_id=card.article_id,
        canonical_url=card.canonical_url,
        content_hash=content_hash,
        original_text=text,
    )
    draft = _refresh_draft(
        target_id=company.assessment_id,
        changed_claim_ids=(claim.claim_id,),
        changed_dimensions=("customer_sourcing_evidence",),
    )
    service = HotspotMonitoringService(
        repository, FixedClusterer(), FixedRefresher(draft)
    )
    service.add_monitoring_trigger(
        request.run_id,
        MonitoringTriggerDraft(
            target_type="company_assessment",
            target_id=company.assessment_id,
            event_types=("customer_qualification",),
            created_by="analyst",
        ),
    )
    evidence = MonitoringEvidence(
        event_id="monitor-event-new-source",
        event_type="customer_qualification",
        target_type="company_assessment",
        target_id=company.assessment_id,
        evidence_ids=(card.evidence_id,),
        event_time=datetime(2026, 8, 17, 8, tzinfo=timezone.utc),
        published_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        assessment_as_of=datetime(2026, 8, 18, tzinfo=timezone.utc),
        new_claims=(claim,),
        new_evidence_cards=(card,),
        new_source_snapshots=(snapshot,),
    )

    service.refresh(request.run_id, [evidence])

    assert repository.list_monitoring_evidence(request.run_id) == (evidence,)


def test_stage6_industry_trend_uses_independent_recomputation_not_refresher_scores(
    tmp_path,
):
    """SELECT INVARIANT: researcher self-report cannot manufacture score movement."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    stage3 = repository.get_stage3_result(request.run_id)
    old_claim = stage3.claims[0]
    old_card = stage3.evidence_cards[0]
    old_snapshot = stage3.source_snapshots[0]
    text = "A new filing reports a current industry event."
    content_hash = "sha256:" + sha256(text.encode("utf-8")).hexdigest()
    claim = replace(
        old_claim,
        claim_id="monitor-claim-independent-recompute",
        evidence_ids=("monitor-evidence-independent-recompute",),
        statement=text,
        fact_key="monitor-independent-recompute",
        material_field="technical_performance_gap",
        primary_scoring_dimension="technical_performance_gap",
    )
    card = replace(
        old_card,
        evidence_id=claim.evidence_ids[0],
        claim_id=claim.claim_id,
        article_id="monitor-article-independent-recompute",
        canonical_url="https://example.com/monitor/independent-recompute",
        publication_date=date(2026, 8, 17),
        content_hash=content_hash,
        exact_quote=text,
        quote_start=0,
        quote_end=len(text),
        business_fact_assertions=(),
    )
    snapshot = replace(
        old_snapshot,
        article_id=card.article_id,
        canonical_url=card.canonical_url,
        content_hash=content_hash,
        original_text=text,
    )
    malicious = _refresh_draft(
        target_id=company.assessment_id,
        changed_claim_ids=(claim.claim_id,),
        changed_dimensions=("technical_performance_gap",),
        after_score_min=90,
    )
    base_family = company.defensibility
    recomputed = replace(
        malicious,
        before_state=base_family.primary_state,
        after_state=base_family.primary_state,
        before_score_min=base_family.score_min,
        before_score_max=base_family.score_max,
        before_mandatory_gate_level=len(base_family.achieved_gates),
        before_mandatory_floor_valid=base_family.primary_state is not None,
        after_score_min=base_family.score_min,
        after_score_max=base_family.score_max,
        after_mandatory_gate_level=len(base_family.achieved_gates),
        after_mandatory_floor_valid=base_family.primary_state is not None,
        recompute_receipt_id="canonical-company-recompute-1",
        executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
        executable_contract_sha256=(
            "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
        ),
    )
    class Evaluator:
        def recompute(self, **kwargs):
            return _raw_monitoring_observation(
                target_type=recomputed.target_type,
                target_id=recomputed.target_id,
                changed_claim_ids=recomputed.changed_claim_ids,
                changed_dimensions=recomputed.changed_dimensions,
                explanation="Evaluator observed a changed fact candidate.",
                trace_id="canonical-company-observation-1",
            )

    service = HotspotMonitoringService(
        repository,
        FixedClusterer(),
        FixedRefresher(malicious),
        canonical_recomputer=CanonicalMonitoringRecomputer(
            repository,
            Evaluator(),
            Stage34CanonicalCalculator(
                repository,
                FixedRawCanonicalScorer(
                    _canonical_result_from_draft(recomputed),
                    "canonical-company-scorer-raw-1",
                ),
            ),
        ),
    )
    service.add_monitoring_trigger(
        request.run_id,
        MonitoringTriggerDraft(
            target_type="company_assessment",
            target_id=company.assessment_id,
            event_types=("independent_recompute",),
            created_by="analyst",
        ),
    )
    source_identity = repository.save_source_identity(
        CanonicalSourceIdentityResolver(
            _VerifiedOriginalResolverClient()
        ).resolve(snapshot.canonical_url),
        canonical_publisher_id="publisher:example",
        canonical_document_id=snapshot.article_id,
        origin_event_id="event:independent-recompute",
    )
    source_version = repository.save_source_version(
        source_identity_id=source_identity.source_identity_id,
        retrieved_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        raw_bytes=text.encode("utf-8"),
        normalized_content=text,
        quote_span=(0, len(text), text),
        content_type="text/html",
        language="en",
        publication_time=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        updated_time=None,
    )
    evidence = MonitoringEvidence(
        event_id="monitor-event-independent-recompute",
        event_type="independent_recompute",
        target_type="company_assessment",
        target_id=company.assessment_id,
        evidence_ids=(card.evidence_id,),
        event_time=datetime(2026, 8, 17, 8, tzinfo=timezone.utc),
        published_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        assessment_as_of=datetime(2026, 8, 18, tzinfo=timezone.utc),
        new_claims=(claim,),
        new_evidence_cards=(card,),
        new_source_snapshots=(snapshot,),
        source_version_ids=(source_version.source_version_id,),
        governance_bundle_sha256=CANONICAL_GOVERNANCE_BUNDLE_SHA256,
    )

    result = service.refresh(request.run_id, [evidence])

    assert result.changes[0].trend_state == "unchanged"
    assert result.changes[0].recompute_receipt_id.startswith(
        "canonical_scoring_reconciliation_"
    )


def test_stage6_rejects_industry_recompute_before_values_not_bound_to_persisted_snapshot():
    """SELECT INVARIANT: an independent scorer cannot rewrite the persisted before state."""
    persisted = PersistedAssessmentSnapshot(
        state="high_defensibility",
        score_min=75.0,
        score_max=75.0,
        mandatory_gate_level=1,
        mandatory_floor_valid=True,
        executable_contract_id="contract-id",
        executable_contract_sha256="a" * 64,
    )
    unbound = _refresh_draft(
        before_state="low_defensibility",
        before_score_min=10,
        before_score_max=20,
        before_mandatory_gate_level=0,
        before_mandatory_floor_valid=False,
    )

    with pytest.raises(ValueError, match="persisted base snapshot"):
        _assert_persisted_before_snapshot(unbound, persisted)


def test_canonical_monitoring_recomputer_derives_before_values_from_repository(tmp_path):
    """SELECT INVARIANT: production recomputation has no writer for arbitrary before values."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    base = company.defensibility

    class Evaluator:
        def recompute(self, *, base_run_id, target_type, target_id, evidence):
            return _raw_monitoring_observation(
                target_type=target_type,
                target_id=target_id,
                changed_claim_ids=("new-claim",),
                changed_dimensions=("technical_performance_gap",),
                explanation="Evaluator observed a changed fact candidate.",
                trace_id="canonical-evaluator-observation-1",
            )

    scorer = FixedRawCanonicalScorer(
        CanonicalAssessmentResult(
            target_type="company_assessment",
            target_id=company.assessment_id,
            after_state=base.primary_state,
            after_score_min=base.score_min,
            after_score_max=base.score_max,
            after_mandatory_gate_level=len(base.achieved_gates),
            after_mandatory_floor_valid=True,
            changed_claim_ids=("new-claim",),
            changed_dimensions=("technical_performance_gap",),
            explanation="Canonical company scorer recomputed the frozen family.",
            recompute_receipt_id="canonical-scorer-1",
            executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
            executable_contract_sha256=(
                "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
            ),
        ),
        "canonical-scorer-raw-before-values",
    )

    evidence = _verified_monitoring_evidence(
        repository,
        target_type="company_assessment",
        target_id=company.assessment_id,
        event_id="canonical-before-values-event",
    )
    calculator = Stage34CanonicalCalculator(repository, scorer)
    draft = CanonicalMonitoringRecomputer(
        repository, Evaluator(), calculator
    ).refresh(
        base_run_id=request.run_id,
        target_type="company_assessment",
        target_id=company.assessment_id,
        evidence=(evidence,),
    )

    assert draft.before_state == base.primary_state
    assert draft.before_score_min == base.score_min
    assert draft.before_score_max == base.score_max
    assert draft.before_mandatory_gate_level == len(base.achieved_gates)
    assert draft.after_state == base.primary_state
    assert draft.after_score_min == base.score_min
    assert draft.recompute_receipt_id.startswith(
        "canonical_scoring_reconciliation_"
    )
    assert len(calculator.calls) == 1


def test_arbitrary_scorer_shaped_object_cannot_invent_empty_evidence_after_state(
    tmp_path,
):
    """SELECT INVARIANT: only the concrete Stage 3/4 calculator owns after-state."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request, assessment = _minimal_canonical_segment_target(repository)
    evaluator = SimpleNamespace(
        recompute=lambda **_kwargs: _raw_monitoring_observation(
            target_type="segment_assessment",
            target_id=assessment.segment_id,
            changed_claim_ids=("invented-claim",),
            changed_dimensions=("supply_concentration",),
            explanation="Caller proposes a state with no evidence.",
            trace_id="arbitrary-scorer-empty-evidence-observation",
        )
    )
    arbitrary_scorer = FixedRawCanonicalScorer(
        CanonicalAssessmentResult(
            target_type="segment_assessment",
            target_id=assessment.segment_id,
            after_state="caller_invented_state",
            after_score_min=99,
            after_score_max=99,
            after_mandatory_gate_level=999,
            after_mandatory_floor_valid=True,
            changed_claim_ids=("invented-claim",),
            changed_dimensions=("supply_concentration",),
            explanation="Caller invented an authoritative result.",
            recompute_receipt_id="caller-invented-receipt",
            executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
            executable_contract_sha256=(
                "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
            ),
        ),
        "arbitrary-scorer-empty-evidence-result",
    )

    with pytest.raises(ValueError, match="concrete Stage 3/4 canonical calculator"):
        CanonicalMonitoringRecomputer(
            repository,
            evaluator,
            arbitrary_scorer,
        ).refresh(
            base_run_id=request.run_id,
            target_type="segment_assessment",
            target_id=assessment.segment_id,
            evidence=(),
        )


def test_concrete_calculator_rejects_invented_empty_evidence_transition(tmp_path):
    """SELECT INVARIANT: concrete authority rejects illegal state/range/gate output."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request, assessment = _minimal_canonical_segment_target(repository)
    evaluator = SimpleNamespace(
        recompute=lambda **_kwargs: _raw_monitoring_observation(
            target_type="segment_assessment",
            target_id=assessment.segment_id,
            changed_claim_ids=("invented-claim",),
            changed_dimensions=("supply_concentration",),
            explanation="No evidence supports this transition.",
            trace_id="concrete-empty-evidence-observation",
        )
    )
    executor = FixedRawCanonicalScorer(
        CanonicalAssessmentResult(
            target_type="segment_assessment",
            target_id=assessment.segment_id,
            after_state="caller_invented_state",
            after_score_min=99,
            after_score_max=99,
            after_mandatory_gate_level=999,
            after_mandatory_floor_valid=True,
            changed_claim_ids=("invented-claim",),
            changed_dimensions=("supply_concentration",),
            explanation="Invented result.",
            recompute_receipt_id="invented",
            executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
            executable_contract_sha256=(
                "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
            ),
        ),
        "concrete-empty-evidence-result",
    )

    with pytest.raises(ValueError, match="canonical Stage 3/4 result"):
        CanonicalMonitoringRecomputer(
            repository,
            evaluator,
            Stage34CanonicalCalculator(repository, executor),
        ).refresh(
            base_run_id=request.run_id,
            target_type="segment_assessment",
            target_id=assessment.segment_id,
            evidence=(),
        )


def test_concrete_calculator_accepts_verified_evidence_and_binds_governance(tmp_path):
    """SELECT INVARIANT: legal recomputation binds the exact bundle and child hashes."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request, assessment = _minimal_canonical_segment_target(repository)
    evidence = _verified_segment_monitoring_evidence(repository, assessment)
    evaluator = SimpleNamespace(
        recompute=lambda **_kwargs: _raw_monitoring_observation(
            target_type="segment_assessment",
            target_id=assessment.segment_id,
            changed_claim_ids=("canonical-new-claim",),
            changed_dimensions=("supply_concentration",),
            explanation="Verified evidence requires canonical recomputation.",
            trace_id="verified-canonical-observation",
        )
    )
    executor = FixedRawCanonicalScorer(
        CanonicalAssessmentResult(
            target_type="segment_assessment",
            target_id=assessment.segment_id,
            after_state=assessment.primary_state,
            after_score_min=assessment.score_min,
            after_score_max=assessment.score_max,
            after_mandatory_gate_level=len(assessment.achieved_gates),
            after_mandatory_floor_valid=True,
            changed_claim_ids=("canonical-new-claim",),
            changed_dimensions=("supply_concentration",),
            explanation="Concrete calculator recomputed the Stage 3 state.",
            recompute_receipt_id="pending",
            executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
            executable_contract_sha256=(
                "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
            ),
        ),
        "verified-canonical-result",
    )
    draft = CanonicalMonitoringRecomputer(
        repository,
        evaluator,
        Stage34CanonicalCalculator(repository, executor),
    ).refresh(
        base_run_id=request.run_id,
        target_type="segment_assessment",
        target_id=assessment.segment_id,
        evidence=(evidence,),
    )

    assert draft.after_state == assessment.primary_state
    assert draft.after_score_min == assessment.score_min
    with sqlite3.connect(repository.db_path) as connection:
        payload_json = connection.execute(
            "SELECT request_payload_json FROM theme_chokepoint_canonical_scoring_requests"
        ).fetchone()[0]
    payload = json.loads(payload_json)
    assert payload["governance_bundle_id"] == CANONICAL_GOVERNANCE_BUNDLE_ID
    assert payload["governance_bundle_sha256"] == CANONICAL_GOVERNANCE_BUNDLE_SHA256
    assert set(payload["governance_artifact_sha256"]) == {
        "runtime_overlay",
        "business_fact_decision_table",
        "source_identity_schema",
        "source_identity_policy",
    }


def test_canonical_calculator_executes_stage4_for_verified_dimension_transition(
    tmp_path,
):
    """SELECT INVARIANT: verified changes run the Stage 4 family calculator."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    base = company.defensibility
    evidence = _verified_monitoring_evidence(
        repository,
        target_type="company_assessment",
        target_id=company.assessment_id,
        claim_id="canonical-stage4-transition-claim",
        dimension="technical_performance_gap",
        event_id="canonical-stage4-transition-event",
    )
    update = DimensionRatingDraft(
        dimension="technical_performance_gap",
        rating_min=0,
        rating_max=0,
        evidence_state="supported",
        bound_type="exact",
        bound_basis=BoundBasis(
            floor_anchor=0,
            ceiling_anchor=0,
            exact_basis="direct_upper_bound",
            excluded_higher_anchors=(1, 2, 3, 4),
        ),
        evidence_ids=(evidence.evidence_ids[0],),
        stale=False,
        rationale="Verified evidence excludes a current performance gap.",
        anchor_conditions=(
            AnchorConditionResult(
                condition_id="technical_performance_gap.anchor_0.floor",
                condition_type="floor",
                evidence_state="supported",
                evidence_ids=(evidence.evidence_ids[0],),
                decisive_claim_ids=(evidence.new_claims[0].claim_id,),
                anchor=0,
            ),
            AnchorConditionResult(
                condition_id="technical_performance_gap.anchor_0.ceiling",
                condition_type="ceiling",
                evidence_state="supported",
                evidence_ids=(evidence.evidence_ids[0],),
                decisive_claim_ids=(evidence.new_claims[0].claim_id,),
                anchor=0,
                excluded_higher_anchors=(1, 2, 3, 4),
            ),
        ),
        decisive_evidence_ids=(evidence.evidence_ids[0],),
    )

    class Stage4ProposalScorer:
        def score(self, **_kwargs):
            payload = asdict(
                CanonicalAssessmentResult(
                    target_type="company_assessment",
                    target_id=company.assessment_id,
                    after_state="high_defensibility",
                    after_score_min=80.0,
                    after_score_max=80.0,
                    after_mandatory_gate_level=1,
                    after_mandatory_floor_valid=True,
                    changed_claim_ids=(evidence.new_claims[0].claim_id,),
                    changed_dimensions=("technical_performance_gap",),
                    explanation="Canonical Stage 4 recomputed verified evidence.",
                    recompute_receipt_id="pending",
                    executable_contract_id=(
                        "theme-chokepoint-semantic-runtime-overlay-v1.4.1"
                    ),
                    executable_contract_sha256=(
                        "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
                    ),
                )
            )
            payload.pop("recompute_receipt_id")
            payload.pop("relief_assessment")
            payload["dimension_updates"] = [asdict(update)]
            return CanonicalScoringRawProviderResponse(
                provider="controlled-stage4-proposal",
                provider_trace_id="canonical-stage4-transition-score",
                http_status=200,
                raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
                retrieved_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
                cost_usd=0.01,
            )

    evaluator = SimpleNamespace(
        recompute=lambda **_kwargs: _raw_monitoring_observation(
            target_type="company_assessment",
            target_id=company.assessment_id,
            changed_claim_ids=(evidence.new_claims[0].claim_id,),
            changed_dimensions=("technical_performance_gap",),
            explanation="Verified evidence changed one Stage 4 input.",
            trace_id="canonical-stage4-transition-observation",
        )
    )
    draft = CanonicalMonitoringRecomputer(
        repository,
        evaluator,
        Stage34CanonicalCalculator(repository, Stage4ProposalScorer()),
    ).refresh(
        base_run_id=request.run_id,
        target_type="company_assessment",
        target_id=company.assessment_id,
        evidence=(evidence,),
    )

    assert base.score_min == 100.0
    assert draft.after_score_min == 80.0
    assert draft.after_score_max == 80.0
    assert draft.after_state == "high_defensibility"


def test_canonical_monitoring_consumer_reloads_reconciled_result(tmp_path):
    """SELECT INVARIANT: after-state is consumed only from the stored four-record chain."""

    class TamperingRepository(ThemeChokepointRepository):
        def reconcile_canonical_scoring(self, record):
            saved = super().reconcile_canonical_scoring(record)
            with sqlite3.connect(self.db_path) as connection:
                connection.execute(
                    "UPDATE theme_chokepoint_canonical_scoring_results "
                    "SET parsed_payload_json = ? WHERE result_record_id = ?",
                    ('{"forged":true}', record.result_record_id),
                )
            return saved

    repository = TamperingRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    base = company.defensibility
    evaluator = SimpleNamespace(
        recompute=lambda **_kwargs: _raw_monitoring_observation(
            target_type="company_assessment",
            target_id=company.assessment_id,
            changed_claim_ids=("new-claim",),
            changed_dimensions=("technical_performance_gap",),
            explanation="Evaluator observed a changed fact candidate.",
            trace_id="canonical-reload-observation",
        )
    )
    scorer = FixedRawCanonicalScorer(
        CanonicalAssessmentResult(
            target_type="company_assessment",
            target_id=company.assessment_id,
            after_state=base.primary_state,
            after_score_min=base.score_min,
            after_score_max=base.score_max,
            after_mandatory_gate_level=len(base.achieved_gates),
            after_mandatory_floor_valid=True,
            changed_claim_ids=("new-claim",),
            changed_dimensions=("technical_performance_gap",),
            explanation="Canonical scorer result.",
            recompute_receipt_id="ignored-local-receipt",
            executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
            executable_contract_sha256=(
                "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
            ),
        ),
        "canonical-reload-scorer",
    )

    with pytest.raises(ValueError, match="stored canonical scoring result"):
        CanonicalMonitoringRecomputer(
            repository,
            evaluator,
            Stage34CanonicalCalculator(repository, scorer),
        ).refresh(
            base_run_id=request.run_id,
            target_type="company_assessment",
            target_id=company.assessment_id,
            evidence=(
                _verified_monitoring_evidence(
                    repository,
                    target_type="company_assessment",
                    target_id=company.assessment_id,
                    event_id="canonical-reload-event",
                ),
            ),
        )


def test_monitoring_recomputer_rejects_raw_evaluator_after_state_fields(tmp_path):
    """SELECT INVARIANT: evaluator raw JSON may propose observations, never after-state."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    base = company.defensibility
    evaluator = SimpleNamespace(
        recompute=lambda **_kwargs: _raw_monitoring_result(
            CanonicalAssessmentResult(
                target_type="company_assessment",
                target_id=company.assessment_id,
                after_state="high_defensibility",
                after_score_min=90,
                after_score_max=90,
                after_mandatory_gate_level=2,
                after_mandatory_floor_valid=True,
                changed_claim_ids=("new-claim",),
                changed_dimensions=("technical_performance_gap",),
                explanation="Evaluator self-reports a stronger assessment.",
                recompute_receipt_id="evaluator-self-report",
                executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
                executable_contract_sha256=(
                    "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
                ),
            ),
            "evaluator-self-report",
        )
    )

    with pytest.raises(ValueError, match="must not contain after-state fields"):
        CanonicalMonitoringRecomputer(
            repository,
            evaluator,
            Stage34CanonicalCalculator(repository, FixedCanonicalScorer(None)),
        ).refresh(
            base_run_id=request.run_id,
            target_type="company_assessment",
            target_id=company.assessment_id,
            evidence=(),
        )


def test_canonical_scorer_typed_result_without_raw_lineage_is_rejected(tmp_path):
    """SELECT INVARIANT: a typed scorer result cannot replace persisted raw lineage."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    base = company.defensibility
    evaluator = SimpleNamespace(
        recompute=lambda **_kwargs: _raw_monitoring_observation(
            target_type="company_assessment",
            target_id=company.assessment_id,
            changed_claim_ids=("new-claim",),
            changed_dimensions=("technical_performance_gap",),
            explanation="Evaluator observed a changed fact candidate.",
            trace_id="typed-result-without-raw-lineage",
        )
    )
    typed_result = CanonicalAssessmentResult(
        target_type="company_assessment",
        target_id=company.assessment_id,
        after_state=base.primary_state,
        after_score_min=base.score_min,
        after_score_max=base.score_max,
        after_mandatory_gate_level=len(base.achieved_gates),
        after_mandatory_floor_valid=True,
        changed_claim_ids=("new-claim",),
        changed_dimensions=("technical_performance_gap",),
        explanation="Unpersisted typed canonical result.",
        recompute_receipt_id="typed-result-only",
        executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
        executable_contract_sha256=(
            "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
        ),
    )

    with pytest.raises(
        ValueError, match="canonical scoring request/raw/result/reconciliation"
    ):
        CanonicalMonitoringRecomputer(
            repository,
            evaluator,
            Stage34CanonicalCalculator(
                repository, FixedCanonicalScorer(typed_result)
            ),
        ).refresh(
            base_run_id=request.run_id,
            target_type="company_assessment",
            target_id=company.assessment_id,
            evidence=(
                _verified_monitoring_evidence(
                    repository,
                    target_type="company_assessment",
                    target_id=company.assessment_id,
                    event_id="typed-result-event",
                ),
            ),
        )


def test_assessment_revision_is_append_only_and_head_advances(tmp_path):
    """SELECT INVARIANT: a new assessment is an immutable revision behind one head."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    target = (request.run_id, "company_assessment", company.assessment_id)

    before_head = repository.get_assessment_head(*target)
    revision = SimpleNamespace(
        revision_id="assessment-revision-1",
        run_id=request.run_id,
        target_type="company_assessment",
        target_id=company.assessment_id,
        parent_revision_id=before_head.revision_id,
        result_reconciliation_id="canonical-reconciliation-1",
    )
    repository.append_assessment_revision(revision)
    after_head = repository.get_assessment_head(*target)

    assert after_head.revision_id == revision.revision_id
    assert repository.get_assessment_revision(before_head.revision_id) == before_head.revision
    assert repository.get_assessment_revision(revision.revision_id) == revision


def test_assessment_head_cas_rejects_stale_expected_revision(tmp_path):
    """SELECT INVARIANT: stale expected head cannot overwrite a newer assessment revision."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    target = (request.run_id, "company_assessment", company.assessment_id)

    with pytest.raises(ValueError, match="stale expected assessment head"):
        repository.compare_and_swap_assessment_head(
            *target,
            expected_revision_id="assessment-revision-stale-writer-read",
            new_revision_id="assessment-revision-stale-writer",
        )


def test_outcome_unknown_retry_reconciles_same_operation_without_provider_replay(tmp_path):
    """SELECT INVARIANT: OUTCOME_UNKNOWN retries reconcile one operation before I/O."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    provider_calls = []

    class Evaluator:
        def recompute(self, **_kwargs):
            provider_calls.append("called")
            return _raw_monitoring_observation(
                target_type="company_assessment",
                target_id=company.assessment_id,
                changed_claim_ids=("new-claim",),
                changed_dimensions=("technical_performance_gap",),
                explanation="Evaluator observed a changed fact candidate.",
                trace_id="outcome-unknown-provider-trace",
            )

    class OutcomeUnknownScorer:
        def score(self, **_kwargs):
            raise TimeoutError("canonical scorer outcome unknown after raw persistence")

    recomputer = CanonicalMonitoringRecomputer(
        repository,
        Evaluator(),
        Stage34CanonicalCalculator(repository, OutcomeUnknownScorer()),
    )
    evidence = _verified_monitoring_evidence(
        repository,
        target_type="company_assessment",
        target_id=company.assessment_id,
        event_id="outcome-unknown-event",
    )
    with pytest.raises(TimeoutError, match="outcome unknown"):
        recomputer.refresh(
            base_run_id=request.run_id,
            target_type="company_assessment",
            target_id=company.assessment_id,
            evidence=(evidence,),
        )
    operation = repository.list_monitoring_evaluator_requests(request.run_id)[0]
    assert len(repository.list_monitoring_evaluator_raw_responses(request.run_id)) == 1

    reconciled = recomputer.reconcile_outcome_unknown(
        operation_id=operation.request_record_id
    )

    assert reconciled.operation_id == operation.request_record_id
    assert len(provider_calls) == 1


def test_monitoring_recomputer_persists_request_raw_response_and_parsed_result(
    tmp_path,
):
    """SELECT INVARIANT: canonical recompute lineage is created at real boundaries."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    base = company.defensibility

    class RawEvaluator:
        def recompute(self, **_kwargs):
            return _raw_monitoring_observation(
                target_type="company_assessment",
                target_id=company.assessment_id,
                changed_claim_ids=("new-claim",),
                changed_dimensions=("technical_performance_gap",),
                explanation="Evaluator observed a changed fact candidate.",
                trace_id="monitor-provider-trace-1",
            )

    scorer = FixedRawCanonicalScorer(
        CanonicalAssessmentResult(
            target_type="company_assessment",
            target_id=company.assessment_id,
            after_state=base.primary_state,
            after_score_min=base.score_min,
            after_score_max=base.score_max,
            after_mandatory_gate_level=len(base.achieved_gates),
            after_mandatory_floor_valid=True,
            changed_claim_ids=("new-claim",),
            changed_dimensions=("technical_performance_gap",),
            explanation="Canonical scorer result.",
            recompute_receipt_id="canonical-scorer-receipt-1",
            executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
            executable_contract_sha256="c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03",
        ),
        "canonical-scorer-raw-lineage-1",
    )
    evidence = _verified_monitoring_evidence(
        repository,
        target_type="company_assessment",
        target_id=company.assessment_id,
        event_id="canonical-raw-lineage-event",
    )
    calculator = Stage34CanonicalCalculator(repository, scorer)
    recomputer = CanonicalMonitoringRecomputer(
        repository, RawEvaluator(), calculator
    )
    draft = recomputer.refresh(
        base_run_id=request.run_id,
        target_type="company_assessment",
        target_id=company.assessment_id,
        evidence=(evidence,),
    )

    assert draft.recompute_receipt_id.startswith(
        "canonical_scoring_reconciliation_"
    )
    assert len(calculator.calls) == 1
    assert len(repository.list_monitoring_evaluator_requests(request.run_id)) == 1
    assert len(repository.list_monitoring_evaluator_raw_responses(request.run_id)) == 1
    assert len(repository.list_monitoring_evaluator_results(request.run_id)) == 1


@pytest.mark.parametrize(
    ("column", "forged_value"),
    (
        ("raw_body", b'{"forged":true}'),
        ("provider_trace_id", "forged-trace"),
        ("run_id", "wrong-run"),
    ),
)
def test_monitoring_recomputer_reconciles_stored_raw_record_before_scoring(
    tmp_path, column, forged_value
):
    """SELECT INVARIANT: stored hash/run/trace mutation fails closed before scoring."""
    class TamperingRepository(ThemeChokepointRepository):
        def save_monitoring_evaluator_raw_response(self, record):
            saved = super().save_monitoring_evaluator_raw_response(record)
            with sqlite3.connect(self.db_path) as connection:
                connection.execute(
                    f"UPDATE theme_chokepoint_monitor_evaluator_raw_responses SET {column} = ? WHERE response_record_id = ?",
                    (forged_value, record.response_record_id),
                )
            return saved

    repository = TamperingRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    base = company.defensibility
    result = CanonicalAssessmentResult(
        target_type="company_assessment",
        target_id=company.assessment_id,
        after_state=base.primary_state,
        after_score_min=base.score_min,
        after_score_max=base.score_max,
        after_mandatory_gate_level=len(base.achieved_gates),
        after_mandatory_floor_valid=True,
        changed_claim_ids=("new-claim",),
        changed_dimensions=("technical_performance_gap",),
        explanation="Canonical raw evaluator response.",
        recompute_receipt_id="ignored-self-report",
        executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
        executable_contract_sha256="c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03",
    )
    evaluator = SimpleNamespace(
        recompute=lambda **_kwargs: _raw_monitoring_observation(
            target_type=result.target_type,
            target_id=result.target_id,
            changed_claim_ids=result.changed_claim_ids,
            changed_dimensions=result.changed_dimensions,
            explanation="Evaluator observed a changed fact candidate.",
            trace_id="monitor-trace",
        )
    )

    with pytest.raises(ValueError, match="stored raw response reconciliation"):
        CanonicalMonitoringRecomputer(
            repository,
            evaluator,
            Stage34CanonicalCalculator(
                repository,
                FixedRawCanonicalScorer(result, "canonical-tamper-scorer"),
            ),
        ).refresh(
            base_run_id=request.run_id,
            target_type="company_assessment",
            target_id=company.assessment_id,
            evidence=(),
        )


def test_monitoring_recomputer_rejects_provider_trace_replay(tmp_path):
    """SELECT INVARIANT: one provider trace cannot attest two evaluator requests."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    base = company.defensibility
    result = CanonicalAssessmentResult(
        target_type="company_assessment",
        target_id=company.assessment_id,
        after_state=base.primary_state,
        after_score_min=base.score_min,
        after_score_max=base.score_max,
        after_mandatory_gate_level=len(base.achieved_gates),
        after_mandatory_floor_valid=True,
        changed_claim_ids=("new-claim",),
        changed_dimensions=("technical_performance_gap",),
        explanation="Canonical raw evaluator response.",
        recompute_receipt_id="ignored-self-report",
        executable_contract_id="theme-chokepoint-semantic-runtime-overlay-v1.4.1",
        executable_contract_sha256="c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03",
    )
    evaluator = SimpleNamespace(
        recompute=lambda **_kwargs: _raw_monitoring_observation(
            target_type=result.target_type,
            target_id=result.target_id,
            changed_claim_ids=result.changed_claim_ids,
            changed_dimensions=result.changed_dimensions,
            explanation="Evaluator observed a changed fact candidate.",
            trace_id="replayed-trace",
        )
    )
    recomputer = CanonicalMonitoringRecomputer(
        repository,
        evaluator,
        Stage34CanonicalCalculator(
            repository,
            FixedRawCanonicalScorer(result, "canonical-replay-scorer"),
        ),
    )
    evidence = _verified_monitoring_evidence(
        repository,
        target_type="company_assessment",
        target_id=company.assessment_id,
        event_id="event-one",
    )
    recomputer.refresh(
        base_run_id=request.run_id,
        target_type="company_assessment",
        target_id=company.assessment_id,
        evidence=(evidence,),
    )

    with pytest.raises(ValueError, match="provider trace replay"):
        recomputer.refresh(
            base_run_id=request.run_id,
            target_type="company_assessment",
            target_id=company.assessment_id,
            evidence=(SimpleNamespace(event_id="event-two"),),
        )


@pytest.mark.parametrize("wrapper_variant", (False, True))
def test_stage6_rejects_rewrapped_base_source_under_new_ledger_ids(
    tmp_path, wrapper_variant
):
    """SELECT INVARIANT: new IDs cannot turn an old canonical source into an event."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    stage3 = repository.get_stage3_result(request.run_id)
    old_claim = stage3.claims[0]
    old_card = stage3.evidence_cards[0]
    old_snapshot = next(
        item for item in stage3.source_snapshots if item.article_id == old_card.article_id
    )
    claim = replace(
        old_claim,
        claim_id="rewrapped-claim",
        evidence_ids=("rewrapped-evidence",),
    )
    card = replace(
        old_card,
        evidence_id=claim.evidence_ids[0],
        claim_id=claim.claim_id,
        article_id="rewrapped-article",
        publication_date=date(2026, 8, 17),
    )
    snapshot = replace(old_snapshot, article_id=card.article_id)
    if wrapper_variant:
        wrapped_url = f"{old_snapshot.canonical_url}?utm_source=monitor#same-source"
        wrapped_text = f"{old_snapshot.original_text}\n"
        wrapped_hash = "sha256:" + sha256(wrapped_text.encode("utf-8")).hexdigest()
        card = replace(
            card,
            canonical_url=wrapped_url,
            content_hash=wrapped_hash,
        )
        snapshot = replace(
            snapshot,
            canonical_url=wrapped_url,
            content_hash=wrapped_hash,
            original_text=wrapped_text,
        )
    service = HotspotMonitoringService(
        repository, FixedClusterer(), FixedRefresher(_refresh_draft())
    )
    service.add_monitoring_trigger(
        request.run_id,
        MonitoringTriggerDraft(
            target_type="company_assessment",
            target_id=company.assessment_id,
            event_types=("rewrapped_source",),
            created_by="analyst",
        ),
    )

    with pytest.raises(ValueError, match="already exists in the base ledger"):
        service.refresh(
            request.run_id,
            [
                MonitoringEvidence(
                    event_id="rewrapped-event",
                    event_type="rewrapped_source",
                    target_type="company_assessment",
                    target_id=company.assessment_id,
                    evidence_ids=(card.evidence_id,),
                    event_time=datetime(2026, 8, 17, 8, tzinfo=timezone.utc),
                    published_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
                    assessment_as_of=datetime(2026, 8, 18, tzinfo=timezone.utc),
                    new_claims=(claim,),
                    new_evidence_cards=(card,),
                    new_source_snapshots=(snapshot,),
                )
            ],
        )


def test_stage6_industry_recompute_requires_concrete_canonical_adapter(tmp_path):
    """SELECT INVARIANT: an ObjectRefresher-shaped self-report is not canonical recompute."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")

    with pytest.raises(ValueError, match="CanonicalMonitoringRecomputer"):
        HotspotMonitoringService(
            repository,
            FixedClusterer(),
            FixedRefresher(_refresh_draft()),
            canonical_recomputer=FixedRefresher(_refresh_draft()),
        )


def test_stage6_reused_evidence_cannot_create_transfer_state(tmp_path):
    """SELECT INVARIANT: reused evidence is a revision, never a transfer event."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    draft = _refresh_draft(
        target_id=company.assessment_id,
        before_state=company.competition_primary_state,
        after_state="differentiated_incumbent",
        changed_claim_ids=("claim-powerco-qualified_effective_capacity",),
        changed_dimensions=("qualified_effective_capacity",),
        explanation="Constraint may have moved upstream.",
        former_constraint_easing=True,
        adjacent_constraint_strengthening=False,
    )
    service = HotspotMonitoringService(repository, FixedClusterer(), FixedRefresher(draft))
    service.add_monitoring_trigger(
        request.run_id,
        MonitoringTriggerDraft(
            target_type="company_assessment",
            target_id=company.assessment_id,
            event_types=("capacity_start",),
            created_by="analyst",
        ),
    )

    result = service.refresh(
        request.run_id,
        [
            MonitoringEvidence(
                event_id="monitor-event-2",
                event_type="capacity_start",
                target_type="company_assessment",
                target_id=company.assessment_id,
                evidence_ids=("ev-powerco-qualified_effective_capacity",),
                event_time=datetime(2026, 8, 17, 8, tzinfo=timezone.utc),
                published_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
                assessment_as_of=datetime(2026, 8, 18, tzinfo=timezone.utc),
            )
        ],
    )

    assert result.changes[0].before_state == company.defensibility.primary_state
    assert result.changes[0].after_state == company.defensibility.primary_state
    assert result.changes[0].trend_state == "assessment_revised"


def test_stage6_knowledge_revision_cannot_be_reported_as_industry_strengthening(tmp_path):
    """SELECT INVARIANT: backfilled old evidence is not a new strengthening event."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    draft = _refresh_draft(
        target_id=company.assessment_id,
        before_state=company.competition_primary_state,
        after_state=company.competition_primary_state,
        changed_claim_ids=("claim-powerco-technical_performance_gap",),
        changed_dimensions=("technical_performance_gap",),
        change_type="industry_event",
        explanation="An older filing was indexed late.",
        after_score_min=75,
    )
    service = HotspotMonitoringService(repository, FixedClusterer(), FixedRefresher(draft))
    service.add_monitoring_trigger(
        request.run_id,
        MonitoringTriggerDraft(
            target_type="company_assessment",
            target_id=company.assessment_id,
            event_types=("backfill",),
            created_by="analyst",
        ),
    )

    result = service.refresh(
        request.run_id,
        [
            MonitoringEvidence(
                event_id="monitor-event-3",
                event_type="backfill",
                target_type="company_assessment",
                target_id=company.assessment_id,
                evidence_ids=("ev-powerco-technical_performance_gap",),
                event_time=datetime(2026, 6, 30, tzinfo=timezone.utc),
                published_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
                assessment_as_of=datetime(2026, 8, 17, tzinfo=timezone.utc),
            )
        ],
    )

    assert result.changes[0].trend_state == "assessment_revised"


def test_stage6_reused_evidence_with_future_event_time_is_assessment_revision(tmp_path):
    """SELECT INVARIANT: a future timestamp cannot turn old ledger evidence into an event."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    draft = _refresh_draft(
        target_id=company.assessment_id,
        before_state=company.competition_primary_state,
        after_state=company.competition_primary_state,
        changed_claim_ids=("claim-powerco-technical_performance_gap",),
        changed_dimensions=("technical_performance_gap",),
        after_score_min=75,
    )
    service = HotspotMonitoringService(
        repository, FixedClusterer(), FixedRefresher(draft)
    )
    service.add_monitoring_trigger(
        request.run_id,
        MonitoringTriggerDraft(
            target_type="company_assessment",
            target_id=company.assessment_id,
            event_types=("spoofed_future",),
            created_by="analyst",
        ),
    )

    result = service.refresh(
        request.run_id,
        [
            MonitoringEvidence(
                event_id="monitor-event-future-spoof",
                event_type="spoofed_future",
                target_type="company_assessment",
                target_id=company.assessment_id,
                evidence_ids=("ev-powerco-technical_performance_gap",),
                event_time=datetime(2026, 8, 17, 8, tzinfo=timezone.utc),
                published_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
                assessment_as_of=datetime(2026, 8, 18, tzinfo=timezone.utc),
            )
        ],
    )

    assert result.changes[0].change_type == "knowledge_revision"
    assert result.changes[0].trend_state == "assessment_revised"


def test_stage6_knowledge_revision_is_always_assessment_revised_not_invalidated(tmp_path):
    """SELECT INVARIANT: backfilled old evidence cannot create any business trend."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _ready_stage4(repository)
    PersistentResearchProductService(repository, tmp_path / "artifacts").finalize(
        request.run_id
    )
    company = repository.get_stage4_result(request.run_id).company_assessments[0]
    draft = _refresh_draft(
        target_id=company.assessment_id,
        before_state=company.competition_primary_state,
        after_state=company.competition_primary_state,
        changed_claim_ids=("claim-powerco-technical_performance_gap",),
        changed_dimensions=("technical_performance_gap",),
        change_type="knowledge_revision",
        explanation="An older filing was indexed late.",
        before_score_min=75,
        after_score_min=0,
        before_score_max=90,
        after_score_max=0,
        before_mandatory_gate_level=3,
        after_mandatory_gate_level=0,
        before_mandatory_floor_valid=True,
        after_mandatory_floor_valid=False,
        original_constraint_active=False,
    )
    service = HotspotMonitoringService(repository, FixedClusterer(), FixedRefresher(draft))
    service.add_monitoring_trigger(
        request.run_id,
        MonitoringTriggerDraft(
            target_type="company_assessment",
            target_id=company.assessment_id,
            event_types=("backfill",),
            created_by="analyst",
        ),
    )

    result = service.refresh(
        request.run_id,
        [
            MonitoringEvidence(
                event_id="monitor-event-4",
                event_type="backfill",
                target_type="company_assessment",
                target_id=company.assessment_id,
                evidence_ids=("ev-powerco-technical_performance_gap",),
                event_time=datetime(2026, 6, 30, tzinfo=timezone.utc),
                published_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
                assessment_as_of=datetime(2026, 8, 17, tzinfo=timezone.utc),
            )
        ],
    )

    assert result.changes[0].trend_state == "assessment_revised"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    (
        (
            {
                "before_mandatory_floor_valid": True,
                "after_mandatory_floor_valid": False,
                "after_score_min": 90,
            },
            "invalidated",
        ),
        (
            {
                "original_constraint_active": True,
                "former_constraint_easing": True,
                "adjacent_constraint_strengthening": True,
            },
            "coexisting_constraints",
        ),
        (
            {
                "former_constraint_easing": True,
                "adjacent_constraint_strengthening": True,
                "underlying_demand_intact": True,
            },
            "transferred",
        ),
        ({"after_score_min": 70}, "strengthening"),
        ({"after_mandatory_gate_level": 3}, "strengthening"),
        ({"after_score_max": 70}, "weakening"),
        ({"after_mandatory_gate_level": 1}, "weakening"),
        ({}, "unchanged"),
    ),
)
def test_stage6_derives_industry_trend_by_frozen_priority(overrides, expected):
    """SELECT INVARIANT: industry trends follow the frozen mechanical priority."""
    assert _derive_trend(_refresh_draft(**overrides)) == expected


def test_stage6_consumes_mechanical_relief_transition_without_model_trend_label():
    """SELECT INVARIANT: monitoring consumes Relief output, not proposed trend prose."""
    from test_theme_chokepoint_relief import _period

    periods = tuple(_period(month, adjacent=True) for month in (1, 2, 3))
    relief = ReliefHorizonService().assess(
        base=ReliefScenarioInput("base", 30, "qualified_units_per_bucket", periods),
        stress=ReliefScenarioInput("stress", 30, "qualified_units_per_bucket", periods),
    )

    assert _derive_trend(_refresh_draft(relief_assessment=relief)) == "transferred"
