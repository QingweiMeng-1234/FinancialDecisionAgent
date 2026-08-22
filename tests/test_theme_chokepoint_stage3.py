from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from event_collector.theme_chokepoint import stage3 as stage3_module
from event_collector.theme_chokepoint.contracts import (
    AssessmentScope,
    AnchorConditionResult,
    BoundBasis,
    Claim,
    ClaimDraft,
    CounterSearchRawProviderResponse,
    DemandFrame,
    DependencyEdge,
    DimensionRatingDraft,
    EvidenceCard,
    EvidenceCandidate,
    ProductAnchor,
    ResearchRequest,
    RunStatus,
    SegmentScoreDraft,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.relief import (
    ReliefPeriodInput,
    ReliefRouteOutput,
    ReliefScenarioInput,
)
from event_collector.theme_chokepoint.source_identity import (
    CanonicalSourceIdentityResolver,
    SourceRelation,
    SourceResolutionRawResponse,
)
from event_collector.theme_chokepoint.stage3 import (
    EvidenceChokepointLoop,
    FrozenScoringContract,
    _finalize_assessment,
    _validate_dimension,
)
from event_collector.theme_chokepoint.providers.counter_search import (
    EvidenceBoundCounterSearchProvider,
)


CONTROLLED_GOVERNANCE_BUNDLE_SHA256 = (
    "de5e95275285132e9d147b3d586056fbf3b75de2617b5423d28a6bc320c59e63"
)


def _counter_provider(
    repository, *, include_ambiguous_supply=False, found_routes=frozenset()
):
    class RawExecutor:
        def execute(self, *, route_id, evidence_cards, **_kwargs):
            by_dimension = {
                card.primary_scoring_dimension: card for card in evidence_cards
            }
            evidence_ids = {
                "demand": (by_dimension["demand_pressure"].evidence_id,),
                "supply": (
                    by_dimension["effective_supply_concentration"].evidence_id,
                    *(
                        (by_dimension["qualification_barrier"].evidence_id,)
                        if include_ambiguous_supply
                        else ()
                    ),
                ),
                "alternatives": (by_dimension["substitute_weakness"].evidence_id,),
            }[route_id]
            payload = {
                "query_log_id": f"query-{route_id}",
                "status": "supported" if route_id in found_routes else "explicit_negative",
                "evidence_ids": list(evidence_ids),
                "coverage_state": (
                    "found" if route_id in found_routes else "explicit_negative"
                ),
                "counter_evidence": (
                    [
                        {
                            "canonical_url": f"https://counter.example/{route_id}",
                            "original_text": f"Counter-search source for {route_id}.",
                            "exact_quote": f"Counter-search source for {route_id}.",
                            "claim_statement": f"Counter-search finding for {route_id}.",
                        }
                    ]
                    if route_id in found_routes
                    else []
                ),
                "finding": f"executed {route_id}",
            }
            return CounterSearchRawProviderResponse(
                provider="controlled-search",
                provider_trace_id=f"provider-{route_id}",
                http_status=200,
                raw_body=json.dumps(payload, sort_keys=True).encode("utf-8"),
                retrieved_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
                cost_usd=0.1,
            )

    return EvidenceBoundCounterSearchProvider(
        RawExecutor(),
        repository=repository,
        max_queries=6,
        max_time_seconds=300,
        max_cost_usd=1.0,
    )


def test_stage3_contract_persists_atomic_fact_scope_and_predicate_ledger():
    """SELECT INVARIANT: scoring provenance is first-class data, not inferred text."""
    claim_fields = {
        "fact_key",
        "assessment_scope",
        "condition_ids",
        "claim_capabilities",
        "source_ambiguity",
        "scoring_eligible",
    }
    evidence_fields = claim_fields | {
        "primary_scoring_dimension",
        "scoring_use",
    }
    dimension_fields = {"anchor_conditions", "decisive_evidence_ids"}

    assert claim_fields <= set(Claim.__dataclass_fields__)
    assert evidence_fields <= set(EvidenceCard.__dataclass_fields__)
    assert dimension_fields <= set(DimensionRatingDraft.__dataclass_fields__)


def _runtime_overlay_path() -> Path:
    return (
        Path(stage3_module.__file__).resolve().parents[3]
        / "tools"
        / "theme-chokepoint"
        / "semantic-task-contract-v1.4.json"
    )


def _controlled_loop(*args, **kwargs):
    kwargs.setdefault(
        "expected_contract_sha256",
        "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03",
    )
    kwargs.setdefault("allow_unfrozen_overlay", True)
    kwargs.setdefault(
        "expected_governance_bundle_sha256",
        CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
    )
    return EvidenceChokepointLoop(*args, **kwargs)


def _bound_counter_route(*, run_id="run-stage3-001", assessment_as_of="2026-08-16", **values):
    from event_collector.theme_chokepoint.providers.counter_search import (
        bind_counter_search_route,
    )

    return bind_counter_search_route(
        CounterSearchExecution(**values),
        run_id=run_id,
        assessment_as_of=assessment_as_of,
    )


def test_stage3_default_contract_loader_fails_closed_on_unfrozen_overlay():
    """SELECT INVARIANT: production defaults cannot execute an unfrozen overlay."""
    with pytest.raises(ValueError, match="unfrozen executable scoring contract"):
        FrozenScoringContract()


def test_stage3_controlled_overlay_requires_explicit_matching_sha():
    """SELECT INVARIANT: controlled overlay use is explicit and byte-pinned."""
    overlay = _runtime_overlay_path()
    expected_sha = sha256(overlay.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="disagrees with governance bundle"):
        FrozenScoringContract(
            overlay,
            expected_sha256="0" * 64,
            allow_unfrozen_overlay=True,
            expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
        )

    contract = FrozenScoringContract(
        overlay,
        expected_sha256=expected_sha,
        allow_unfrozen_overlay=True,
        expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
    )
    assert contract.executable_contract_id == (
        "theme-chokepoint-semantic-runtime-overlay-v1.4.1"
    )
    assert contract.executable_contract_sha256 == expected_sha
    assert contract.governance_status == "controlled_unfrozen"


def test_stage3_and_stage4_results_require_executable_contract_lineage():
    """SELECT INVARIANT: every persisted score binds the exact machine contract."""
    from event_collector.theme_chokepoint.contracts import Stage3Result, Stage4Result

    required = {"executable_contract_id", "executable_contract_sha256"}
    assert required <= set(Stage3Result.__dataclass_fields__)
    assert required <= set(Stage4Result.__dataclass_fields__)


def test_stage3_supported_bounds_are_research_questions_not_blocking_failures():
    """SELECT INVARIANT: a supported interval does not make the whole run incomplete."""
    bounded = DimensionRatingDraft(
        dimension="demand_pressure",
        rating_min=2,
        rating_max=4,
        evidence_state="supported",
        bound_type="lower_bound",
        bound_basis=BoundBasis(
            floor_anchor=2,
            unresolved_higher_anchors=(3, 4),
        ),
        evidence_ids=("E1",),
        stale=False,
        missing_material_questions=("Can the higher anchors be excluded?",),
    )
    unknown = replace(
        bounded,
        evidence_state="unknown",
        bound_type="none",
        rating_min=0,
        evidence_ids=(),
        bound_basis=BoundBasis(),
    )

    assert stage3_module._has_blocking_unresolved((bounded,)) is False
    assert stage3_module._has_blocking_unresolved((unknown,)) is True


DIMENSIONS = (
    "demand_pressure",
    "downstream_criticality",
    "effective_supply_concentration",
    "qualification_barrier",
    "capacity_inelasticity",
    "substitute_weakness",
)
SEGMENT_WEIGHTS = {
    "demand_pressure": 15,
    "downstream_criticality": 20,
    "effective_supply_concentration": 15,
    "qualification_barrier": 15,
    "capacity_inelasticity": 15,
    "substitute_weakness": 20,
}


def _resolved_dimension(name, score):
    return DimensionRatingDraft(
        dimension=name,
        rating_min=score,
        rating_max=score,
        evidence_state="supported",
        bound_type="exact",
        bound_basis=BoundBasis(
            floor_anchor=score,
            ceiling_anchor=score,
            exact_basis="natural_cap" if score == 4 else "direct_upper_bound",
            excluded_higher_anchors=tuple(range(score + 1, 5)),
        ),
        evidence_ids=("E-state",),
        stale=False,
        anchor_conditions=(
            AnchorConditionResult(
                condition_id=f"{name}.anchor_{score}.floor",
                condition_type="floor",
                evidence_state="supported",
                evidence_ids=("E-state",),
                decisive_claim_ids=("claim-state",),
                anchor=score,
            ),
            *(
                (
                    AnchorConditionResult(
                        condition_id=f"{name}.anchor_{score}.ceiling",
                        condition_type="ceiling",
                        evidence_state="supported",
                        evidence_ids=("E-state",),
                        decisive_claim_ids=("claim-state",),
                        anchor=score,
                        excluded_higher_anchors=tuple(range(score + 1, 5)),
                    ),
                )
                if score < 4
                else ()
            ),
        ),
        decisive_evidence_ids=("E-state",),
    )


def test_stage3_exact_below_four_requires_decisive_floor_and_ceiling_conditions():
    """SELECT INVARIANT: exact cannot survive without its evidence-bound predicates."""
    invalid = replace(
        _resolved_dimension("demand_pressure", 3),
        anchor_conditions=(),
        decisive_evidence_ids=(),
    )

    with pytest.raises(ValueError, match="decisive floor and ceiling"):
        _validate_dimension(invalid, {"E-state"})


def test_stage3_exact_conditions_must_bind_the_rated_anchor_and_exclusions():
    """SELECT INVARIANT: a generic condition cannot self-certify an exact ceiling."""
    valid = _resolved_dimension("demand_pressure", 3)
    dimension = replace(
        valid,
        anchor_conditions=tuple(
            replace(condition, anchor=None, excluded_higher_anchors=())
            for condition in valid.anchor_conditions
        ),
    )

    with pytest.raises(ValueError, match="rated anchor.*exclusions"):
        _validate_dimension(dimension, {"E-state"})


def _state_draft(scores):
    dimensions = tuple(
        _resolved_dimension(name, scores[name])
        if name in scores
        else DimensionRatingDraft(
            dimension=name,
            rating_min=0,
            rating_max=4,
            evidence_state="unknown",
            bound_type="none",
            bound_basis=BoundBasis(),
            evidence_ids=(),
            stale=False,
        )
        for name in DIMENSIONS
    )
    return SegmentScoreDraft(
        segment_id="segment-state",
        dimensions=dimensions,
        missing_material_fields=(),
        demand_direct_evidence=True,
        supply_direct_evidence=True,
        counter_evidence_search_complete=False,
        mandatory_conflict=False,
        relief_horizon=None,
    )


def test_stage3_segment_state_machine_withholds_unknown_and_separates_watch_from_fail():
    """SELECT INVARIANT: unknown, Watch and supported Hard Fail are distinct states."""
    unknown = _finalize_assessment(
        _unknown_score("segment-state", missing=()),
        SEGMENT_WEIGHTS,
        set(),
        "theme-chokepoint-scoring-v1.4",
    )
    assert unknown.eligible is False
    assert unknown.primary_state is None
    assert unknown.withheld_reason == "insufficient_evidence"

    watch = _finalize_assessment(
        _state_draft(
            {
                "demand_pressure": 1,
                "downstream_criticality": 1,
                "effective_supply_concentration": 2,
            }
        ),
        SEGMENT_WEIGHTS,
        {"E-state"},
        "theme-chokepoint-scoring-v1.4",
    )
    assert watch.eligible is True
    assert watch.primary_state == "watch_segment"
    assert watch.achieved_gates == ("watch_gate",)

    hard_fail = _finalize_assessment(
        _state_draft({"demand_pressure": 0}),
        SEGMENT_WEIGHTS,
        {"E-state"},
        "theme-chokepoint-scoring-v1.4",
    )
    assert hard_fail.eligible is True
    assert hard_fail.primary_state == "not_supported"
    assert hard_fail.achieved_gates == ("segment_hard_fail",)


def test_stage3_strong_segment_requires_two_constraints_and_high_quality_sources():
    """SELECT INVARIANT: a high score cannot compensate missing Strong evidence gates."""
    full_scores = {name: 4 for name in DIMENSIONS}
    high_draft = replace(
        _state_draft(full_scores),
        counter_evidence_search_complete=True,
    )
    merely_high = _finalize_assessment(
        high_draft,
        SEGMENT_WEIGHTS,
        {"E-state"},
        "theme-chokepoint-scoring-v1.4",
    )
    assert merely_high.primary_state == "candidate_chokepoint"

    qualified = replace(
        high_draft,
        independent_supply_constraint_count=2,
        key_source_quality_high=True,
    )
    strong = _finalize_assessment(
        qualified,
        SEGMENT_WEIGHTS,
        {"E-state"},
        "theme-chokepoint-scoring-v1.4",
    )
    assert strong.primary_state == "strong_candidate_chokepoint"
    assert strong.achieved_gates[-1] == "strong_candidate_gate"


def _request(run_id: str = "run-stage3-001", **overrides) -> ResearchRequest:
    values = {
        "run_id": run_id,
        "theme": "AI data-center power infrastructure",
        "trigger": "AI compute deployment growth",
        "region": "global",
        "as_of_date": date(2026, 8, 16),
        "time_horizon_months": 24,
        "analysis_goal": "identify upstream chokepoints",
        "seed_products": (),
        "seed_companies": (),
        "research_mode": "assisted",
        "max_depth": 3,
        "max_nodes": 30,
        "max_iterations": 2,
        "max_sources": 10,
        "max_time_seconds": 900,
        "max_cost_usd": 10.0,
        "max_product_anchors": 2,
    }
    values.update(overrides)
    return ResearchRequest(**values)


def _seed_run(repository, *, graph_ready: bool = True, request=None):
    request = request or _request()
    repository.create_request(request)
    frame = DemandFrame(
        normalized_theme="ai data center power infrastructure",
        scope="global over 24 months",
        exclusions=(),
        demand_hypothesis="AI capacity raises power-distribution equipment demand.",
        measurable_demand_variables=("data-center MW commissioned",),
        time_horizon_months=24,
        unresolved_questions=(),
    )
    anchor = ProductAnchor(
        anchor_id="anchor-1",
        product_name="large power transformer",
        buyer_or_user="data-center operator",
        demand_variable="MW commissioned",
        theme_link="More AI MW requires transformer capacity.",
        confidence=0.8,
        supporting_evidence_ids=(),
        missing_evidence=(),
        status="proposed",
    )
    repository.save_framing(
        request.run_id,
        frame,
        status=RunStatus.AWAITING_PRODUCT_CONFIRMATION,
        anchors=(anchor,),
    )
    repository.confirm_product_anchors(
        request.run_id, (anchor.anchor_id,), confirmed_by="analyst"
    )
    if graph_ready:
        product = SupplyChainNode(
            node_id="node-product",
            normalized_name="large power transformer",
            node_type="product",
            depth=0,
            status="confirmed",
            description="confirmed product",
            aliases=(),
            product_anchor_id=anchor.anchor_id,
        )
        segment = SupplyChainNode(
            node_id="node-segment",
            normalized_name="grain oriented electrical steel",
            node_type="material_segment",
            depth=1,
            status="proposed",
            description="upstream material segment",
            aliases=("GOES",),
        )
        edge = DependencyEdge(
            edge_id="edge-1",
            downstream_node_id=product.node_id,
            upstream_node_id=segment.node_id,
            relation_type="material_input",
            demand_transmission="Transformer demand raises qualified GOES demand.",
            criticality_hypothesis="Qualified GOES shortages constrain transformer output.",
            substitute_hypothesis="Alternative electrical steel may be inadequate.",
            confidence=0.7,
            supporting_claim_ids=(),
            verification_questions=("Is qualified supply concentrated?",),
            status="proposed",
        )
        repository.save_supply_chain_graph(
            request.run_id,
            nodes=(product, segment),
            edges=(edge,),
            truncation_reasons=(),
        )
    return request


def _unknown_score(node_id: str, missing=("effective_supply_concentration",)):
    dimensions = tuple(
        DimensionRatingDraft(
            dimension=name,
            rating_min=0,
            rating_max=4,
            evidence_state="unknown",
            bound_type="none",
            bound_basis=BoundBasis(),
            evidence_ids=(),
            stale=False,
            rationale=f"No scoring evidence resolves {name}.",
            missing_material_questions=(f"What evidence resolves {name}?",),
        )
        for name in DIMENSIONS
    )
    return SegmentScoreDraft(
        segment_id=node_id,
        dimensions=dimensions,
        missing_material_fields=tuple(missing),
        demand_direct_evidence=False,
        supply_direct_evidence=False,
        counter_evidence_search_complete=False,
        mandatory_conflict=False,
        relief_horizon=None,
    )


def _supported_dimension(name: str, evidence_id: str, claim_id: str):
    return DimensionRatingDraft(
            dimension=name,
            rating_min=3,
            rating_max=3,
            evidence_state="supported",
            bound_type="exact",
            bound_basis=BoundBasis(
                floor_anchor=3,
                ceiling_anchor=3,
                exact_basis="direct_upper_bound",
                excluded_higher_anchors=(4,),
            ),
            evidence_ids=(evidence_id,),
            stale=False,
            rationale=f"Original-source evidence resolves {name} at anchor 3.",
            missing_material_questions=(),
            anchor_conditions=(
                AnchorConditionResult(
                    condition_id=f"{name}.anchor_3.floor",
                    condition_type="floor",
                    evidence_state="supported",
                    evidence_ids=(evidence_id,),
                    decisive_claim_ids=(claim_id,),
                    anchor=3,
                ),
                AnchorConditionResult(
                    condition_id=f"{name}.anchor_3.ceiling",
                    condition_type="ceiling",
                    evidence_state="supported",
                    evidence_ids=(evidence_id,),
                    decisive_claim_ids=(claim_id,),
                    anchor=3,
                    excluded_higher_anchors=(4,),
                ),
            ),
            decisive_evidence_ids=(evidence_id,),
    )


def _supported_score(node_id: str, evidence_id: str):
    dimensions = tuple(
        _supported_dimension(name, evidence_id, "claim-1") for name in DIMENSIONS
    )
    return SegmentScoreDraft(
        segment_id=node_id,
        dimensions=dimensions,
        missing_material_fields=(),
        demand_direct_evidence=True,
        supply_direct_evidence=True,
        counter_evidence_search_complete=True,
        mandatory_conflict=False,
        relief_horizon="12_to_24_months",
    )


class ProgressiveScorer:
    def __init__(self):
        self.calls = []

    def assess(self, node, claims, evidence_cards, *, request, contract_version):
        self.calls.append(
            (
                node.node_id,
                tuple(claims),
                tuple(evidence_cards),
                contract_version,
                request,
            )
        )
        if not evidence_cards:
            return _unknown_score(node.node_id)
        claim_by_id = {claim.claim_id: claim for claim in claims}
        evidence_by_dimension = {}
        for card in evidence_cards:
            claim = claim_by_id[card.claim_id]
            evidence_by_dimension[claim.primary_scoring_dimension] = (card, claim)
        unknown_by_name = {
            item.dimension: item for item in _unknown_score(node.node_id).dimensions
        }
        dimensions = tuple(
            _supported_dimension(
                name,
                evidence_by_dimension[name][0].evidence_id,
                evidence_by_dimension[name][1].claim_id,
            )
            if name in evidence_by_dimension
            else unknown_by_name[name]
            for name in DIMENSIONS
        )
        missing = tuple(name for name in DIMENSIONS if name not in evidence_by_dimension)
        return SegmentScoreDraft(
            segment_id=node.node_id,
            dimensions=dimensions,
            missing_material_fields=missing,
            demand_direct_evidence="demand_pressure" in evidence_by_dimension,
            supply_direct_evidence=any(
                name in evidence_by_dimension
                for name in (
                    "effective_supply_concentration",
                    "qualification_barrier",
                    "capacity_inelasticity",
                    "substitute_weakness",
                )
            ),
            counter_evidence_search_complete=not missing,
            mandatory_conflict=False,
            relief_horizon="12_to_24_months" if not missing else None,
        )


class NeverResolvedScorer:
    def assess(self, node, claims, evidence_cards, *, request, contract_version):
        return _unknown_score(node.node_id)


class ReusingOneFactAcrossDimensionsScorer:
    def assess(self, node, claims, evidence_cards, *, request, contract_version):
        if not evidence_cards:
            return _unknown_score(node.node_id)
        return _supported_score(node.node_id, evidence_cards[0].evidence_id)


class RecordingAcquirer:
    def __init__(self, candidates):
        self.candidates = list(candidates)
        self.queries = []

    def acquire(self, *, query, run, node, material_field):
        self.queries.append((query, node.node_id, material_field))
        result, self.candidates = self.candidates, []
        return result


def _candidate(*, url="https://example.com/report?utm_source=test", source_mode="original_text"):
    original = "Capacity is constrained. Qualified supply grew only five percent year over year."
    quote = "Qualified supply grew only five percent year over year."
    start = original.index(quote)
    return EvidenceCandidate(
        article_id="article-1",
        canonical_url=url,
        source_title="Official capacity report",
        publisher="Example Manufacturing",
        source_type="company_filing",
        publication_date=date(2026, 8, 1),
        data_as_of_date=date(2026, 6, 30),
        location="Capacity section",
        quote_start=start,
        quote_end=start + len(quote),
        exact_quote=quote,
        original_text=original,
        source_mode=source_mode,
        stance="supports",
        limitations="Company-reported; no independent denominator.",
        extraction_model="extractor-v1",
        prompt_version="evidence-prompt-v1",
        origin_event_id="event-capacity-2026q2",
        evidence_family_id="family-official-filing",
        claim=ClaimDraft(
            claim_id="claim-1",
            node_id="node-segment",
            claim_type="source_fact",
            statement=quote,
            material_field="effective_supply_concentration",
            primary_scoring_dimension="effective_supply_concentration",
            scoring_use="primary",
            fact_key="qualified_supply_growth_2026q2",
            assessment_scope=AssessmentScope(
                company_id=None,
                product_id="anchor-1",
                segment_id="node-segment",
                customer_or_platform_scope="global over 24 months",
                geography="global",
                time_horizon_months=24,
                as_of_date=date(2026, 8, 16),
            ),
            condition_ids=("effective_supply_concentration.anchor_3.required_growth",),
            claim_capabilities=("general_scoring_evidence",),
        ),
    )


def _all_dimension_candidates():
    first = _candidate()
    candidates = []
    for dimension in DIMENSIONS:
        if dimension == "effective_supply_concentration":
            candidates.append(first)
            continue
        text = f"Original-source fact for {dimension}."
        candidates.append(
            replace(
                first,
                article_id=f"article-{dimension}",
                canonical_url=f"https://example.com/report/{dimension}",
                location=f"{dimension} section",
                quote_start=0,
                quote_end=len(text),
                exact_quote=text,
                original_text=text,
                origin_event_id=f"event-{dimension}-2026q2",
                evidence_family_id=f"family-{dimension}-official",
                claim=replace(
                    first.claim,
                    claim_id=f"claim-{dimension}",
                    statement=text,
                    material_field=dimension,
                    primary_scoring_dimension=dimension,
                    fact_key=f"fact-{dimension}-2026q2",
                    condition_ids=(f"{dimension}.anchor_3.floor",),
                ),
            )
        )
    return candidates


def test_stage3_refuses_to_run_before_supply_chain_graph_is_committed(tmp_path):
    """SELECT INVARIANT: Stage 2 committed state is a hard Stage 3 gate."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository, graph_ready=False)

    with pytest.raises(ValueError, match="SUPPLY_CHAIN_GRAPH_READY"):
        _controlled_loop(
            repository, RecordingAcquirer([]), NeverResolvedScorer()
        ).run(request.run_id)


def test_stage3_stops_before_materializing_a_batch_that_exceeds_cost_budget(tmp_path):
    """SELECT INVARIANT: max_cost_usd is an executed stop boundary with a receipt."""
    from event_collector.theme_chokepoint.contracts import EvidenceAcquisitionBatch

    class CostlyAcquirer:
        def acquire(self, **kwargs):
            return EvidenceAcquisitionBatch(
                candidates=tuple(_all_dimension_candidates()),
                cost_usd=11.0,
                request_receipt_ids=("tavily-request-1",),
            )

    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    result = _controlled_loop(
        repository, CostlyAcquirer(), ProgressiveScorer()
    ).run(request.run_id)

    assert result.status is RunStatus.INCOMPLETE_BUDGET_EXHAUSTED
    assert result.incomplete_reasons == ("max_cost_usd:10.0",)
    assert result.evidence_cards == ()


def test_stage3_stops_on_elapsed_time_budget_before_next_provider_call(tmp_path):
    """SELECT INVARIANT: max_time_seconds is checked by a monotonic runtime clock."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    acquirer = RecordingAcquirer(_all_dimension_candidates())
    ticks = iter((0.0, 901.0, 901.0))
    result = _controlled_loop(
        repository,
        acquirer,
        ProgressiveScorer(),
        monotonic_clock=lambda: next(ticks),
    ).run(request.run_id)

    assert result.status is RunStatus.INCOMPLETE_BUDGET_EXHAUSTED
    assert result.incomplete_reasons == ("max_time_seconds:900",)
    assert acquirer.queries == []


def test_stage3_rejects_one_fact_reused_across_primary_high_dimensions(tmp_path):
    """SELECT INVARIANT: one atomic fact has only one primary 3/4-point owner."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)

    with pytest.raises(ValueError, match="one primary high-score dimension"):
        _controlled_loop(
            repository,
            RecordingAcquirer([_candidate()]),
            ReusingOneFactAcrossDimensionsScorer(),
        ).run(request.run_id)


@pytest.mark.parametrize("mode", ("contradicts_only", "mixed"))
def test_stage3_central_validator_rejects_supported_stance_from_any_scorer(
    tmp_path, mode
):
    """SELECT INVARIANT: scorer injection cannot relabel contradictory evidence supported."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    contradicted = replace(_candidate(), stance="contradicts")
    candidates = [contradicted]
    if mode == "mixed":
        supported = replace(
            _candidate(),
            article_id="article-support",
            canonical_url="https://example.com/report/support",
            origin_event_id="event-capacity-support-2026q2",
            evidence_family_id="family-capacity-support",
            claim=replace(_candidate().claim, claim_id="claim-support"),
        )
        candidates.insert(0, supported)

    class AlternateSegmentScorer:
        def assess(self, node, claims, evidence_cards, *, request, contract_version):
            if not evidence_cards:
                return _unknown_score(node.node_id)
            scoring_ids = tuple(card.evidence_id for card in evidence_cards)
            claim_ids = tuple(claim.claim_id for claim in claims)
            scored = _supported_dimension(
                "effective_supply_concentration", scoring_ids[0], claim_ids[0]
            )
            scored = replace(
                scored,
                evidence_ids=scoring_ids,
                decisive_evidence_ids=scoring_ids,
                anchor_conditions=tuple(
                    replace(
                        condition,
                        evidence_ids=scoring_ids,
                        decisive_claim_ids=claim_ids,
                    )
                    for condition in scored.anchor_conditions
                ),
            )
            unknown = {
                item.dimension: item for item in _unknown_score(node.node_id).dimensions
            }
            return SegmentScoreDraft(
                segment_id=node.node_id,
                dimensions=tuple(
                    scored
                    if name == "effective_supply_concentration"
                    else unknown[name]
                    for name in DIMENSIONS
                ),
                missing_material_fields=(),
                demand_direct_evidence=False,
                supply_direct_evidence=True,
                counter_evidence_search_complete=False,
                mandatory_conflict=False,
                relief_horizon=None,
            )

    with pytest.raises(ValueError, match="supported.*supports stance"):
        _controlled_loop(
            repository,
            RecordingAcquirer(candidates),
            AlternateSegmentScorer(),
        ).run(request.run_id)


def test_stage3_gap_loop_binds_original_span_and_uses_frozen_v14_contract(tmp_path):
    """SELECT INVARIANT: a material gap drives retrieval and only original spans score."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    acquirer = RecordingAcquirer(
        [_candidate(source_mode="search_summary"), *_all_dimension_candidates()]
    )
    scorer = ProgressiveScorer()

    result = _controlled_loop(repository, acquirer, scorer).run(request.run_id)

    assert result.status is RunStatus.CHOKEPOINT_ASSESSMENT_READY
    assert result.iterations_completed == 1
    assert len(acquirer.queries) == 1
    query, node_id, field = acquirer.queries[0]
    assert "grain oriented electrical steel" in query
    assert field == "effective_supply_concentration"
    assert node_id == "node-segment"
    assert {call[3] for call in scorer.calls} == {"theme-chokepoint-scoring-v1.4"}
    assert {call[4] for call in scorer.calls} == {request}
    assert len(result.evidence_cards) == 6
    card = next(item for item in result.evidence_cards if item.claim_id == "claim-1")
    assert card.exact_quote == _candidate().exact_quote
    assert card.content_hash.startswith("sha256:")
    assert card.canonical_url == "https://example.com/report"
    assert card.location == "Capacity section"
    assert card.limitations
    assert card.extraction_model == "extractor-v1"
    claim = next(item for item in result.claims if item.claim_id == "claim-1")
    assert claim.claim_type == "source_fact"
    assert claim.fact_key.startswith("atomic_fact_")
    assert claim.assessment_scope == _candidate().claim.assessment_scope
    assert claim.condition_ids == _candidate().claim.condition_ids
    assert claim.claim_capabilities == ("general_scoring_evidence",)
    assert card.fact_key == claim.fact_key
    assert card.assessment_scope == claim.assessment_scope
    assert card.condition_ids == claim.condition_ids
    assert card.primary_scoring_dimension == "effective_supply_concentration"
    assert card.scoring_use == "primary"
    assert len(result.source_snapshots) == 6
    snapshot = next(
        item for item in result.source_snapshots if item.article_id == card.article_id
    )
    assert snapshot.article_id == card.article_id
    assert snapshot.canonical_url == card.canonical_url
    assert snapshot.original_text == _candidate().original_text
    assert snapshot.content_hash == card.content_hash
    assessment = result.assessments[0]
    assert assessment.score_min == 75.0
    assert assessment.score_max == 75.0
    assert assessment.presence_coverage == 1.0
    assert assessment.resolved_coverage == 1.0
    assert assessment.decision_coverage == 1.0
    assert assessment.primary_state is None
    assert assessment.withheld_reason == "insufficient_evidence"
    assert assessment.critic_receipt is None
    persisted = repository.get_stage3_result(request.run_id)
    assert persisted == result


def test_stage3_ignores_scorer_claim_that_counter_search_is_complete_without_critic_receipt(tmp_path):
    """SELECT INVARIANT: the Ordinal scorer cannot self-authorize Segment Candidate gates."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)

    result = _controlled_loop(
        repository,
        RecordingAcquirer(_all_dimension_candidates()),
        ProgressiveScorer(),
    ).run(request.run_id)

    assessment = result.assessments[0]
    assert assessment.primary_state is None
    assert assessment.withheld_reason == "insufficient_evidence"


def test_stage3_complete_critic_receipt_unlocks_candidate_on_the_default_scoring_path(tmp_path):
    """SELECT INVARIANT: Candidate gates come from a validated critic receipt."""
    from event_collector.theme_chokepoint.contracts import (
        SegmentCounterSearchRoute,
        SegmentCriticReceipt,
    )
    from event_collector.theme_chokepoint.providers.critic import (
        EvidenceBoundSegmentCritic,
    )

    class CompleteCounterSearch:
        def search(self, *, node, ordinal_draft, claims, evidence_cards, request):
            by_dimension = {
                card.primary_scoring_dimension: card for card in evidence_cards
            }
            completed_at = datetime(2026, 8, 16, tzinfo=timezone.utc)
            return SegmentCriticReceipt(
                segment_id=node.node_id,
                protocol_version="segment-counter-search-v1.4",
                query_log_ids=("demand-query", "supply-query", "alternatives-query"),
                stop_reason="protocol_complete",
                unresolved_routes=(),
                demand_evidence_ids=(by_dimension["demand_pressure"].evidence_id,),
                supply_evidence_ids=(
                    by_dimension["effective_supply_concentration"].evidence_id,
                    by_dimension["qualification_barrier"].evidence_id,
                ),
                key_source_evidence_ids=(
                    by_dimension["demand_pressure"].evidence_id,
                    by_dimension["effective_supply_concentration"].evidence_id,
                ),
                completed_at=completed_at,
                route_findings=(
                    _bound_counter_route(
                        route_id="demand",
                        query="named demand counter evidence",
                        query_log_id="demand-query",
                        provider_request_receipt_id="provider-demand",
                        status="supported",
                        evidence_ids=(by_dimension["demand_pressure"].evidence_id,),
                        finding="Demand route retained direct support.",
                        executed_at=completed_at,
                        cost_usd=0.1,
                    ),
                    _bound_counter_route(
                        route_id="supply",
                        query="named supply alternatives",
                        query_log_id="supply-query",
                        provider_request_receipt_id="provider-supply",
                        status="supported",
                        evidence_ids=(
                            by_dimension["effective_supply_concentration"].evidence_id,
                        ),
                        finding="Supply route retained direct support.",
                        executed_at=completed_at,
                        cost_usd=0.1,
                    ),
                    _bound_counter_route(
                        route_id="alternatives",
                        query="named substitute counter evidence",
                        query_log_id="alternatives-query",
                        provider_request_receipt_id="provider-alternatives",
                        status="explicit_negative",
                        evidence_ids=(by_dimension["substitute_weakness"].evidence_id,),
                        finding="No qualified alternative was found in the executed route.",
                        executed_at=completed_at,
                        cost_usd=0.1,
                    ),
                ),
                provider_request_receipt_ids=(
                    "provider-demand",
                    "provider-supply",
                    "provider-alternatives",
                ),
                max_queries=6,
                max_time_seconds=300,
                max_cost_usd=1.0,
                cost_usd_spent=0.3,
                run_id=request.run_id,
                assessment_as_of=request.as_of_date.isoformat(),
            )

    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    result = _controlled_loop(
        repository,
        RecordingAcquirer(_all_dimension_candidates()),
        ProgressiveScorer(),
        critic=EvidenceBoundSegmentCritic(_counter_provider(repository)),
        expected_contract_sha256=(
            "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
        ),
        allow_unfrozen_overlay=True,
        expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
    ).run(request.run_id)

    assert result.assessments[0].primary_state == "candidate_chokepoint"
    assert result.assessments[0].critic_receipt is not None
    assert repository.get_stage3_result(request.run_id) == result


def test_segment_candidate_rejects_route_when_repository_reconciliation_is_missing(
    tmp_path,
):
    """SELECT INVARIANT: a receipt-shaped route cannot replace repository reconciliation."""
    from event_collector.theme_chokepoint.providers.critic import (
        EvidenceBoundSegmentCritic,
    )

    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    result = _controlled_loop(
        repository,
        RecordingAcquirer(_all_dimension_candidates()),
        ProgressiveScorer(),
        critic=EvidenceBoundSegmentCritic(_counter_provider(repository)),
    ).run(request.run_id)
    receipt = result.assessments[0].critic_receipt
    assert receipt is not None
    with repository._connect() as connection:
        connection.execute("DELETE FROM theme_chokepoint_counter_search_reconciliations")

    class ReplayProvider:
        def __init__(self, repository, receipt):
            self.repository = repository
            self.receipt = receipt

        def search(self, **_kwargs):
            return self.receipt

    node = next(item for item in repository.get_supply_chain_graph(request.run_id).nodes if item.depth)
    draft = ProgressiveScorer().assess(
        node,
        result.claims,
        result.evidence_cards,
        request=request,
        contract_version=result.contract_version,
    )
    with pytest.raises(ValueError, match="repository-reconciled counter routes"):
        EvidenceBoundSegmentCritic(ReplayProvider(repository, receipt)).apply(
            node=node,
            ordinal_draft=draft,
            claims=result.claims,
            evidence_cards=result.evidence_cards,
            request=request,
        )


def test_segment_critic_does_not_count_ambiguous_source_family_as_independent_supply(
    tmp_path,
):
    """SELECT INVARIANT: unresolved reprint provenance cannot increase source independence."""
    from event_collector.theme_chokepoint.contracts import (
        SegmentCounterSearchRoute,
        SegmentCriticReceipt,
    )
    from event_collector.theme_chokepoint.providers.critic import (
        EvidenceBoundSegmentCritic,
    )

    candidates = _all_dimension_candidates()
    cards_by_id, claims_by_id, snapshots, fact_keys = {}, {}, {}, set()
    EvidenceChokepointLoop._materialize(
        candidates, cards_by_id, claims_by_id, snapshots, fact_keys
    )
    by_dimension = {
        card.primary_scoring_dimension: card for card in cards_by_id.values()
    }
    ambiguous = by_dimension["qualification_barrier"]
    cards_by_id[ambiguous.evidence_id] = replace(ambiguous, source_ambiguity=True)
    node = SupplyChainNode(
        node_id=candidates[0].claim.node_id,
        normalized_name="qualified supply",
        node_type="equipment_segment",
        depth=1,
        status="supported",
        description="fixture",
        aliases=(),
    )
    request = ResearchRequest(
        run_id="critic-unit",
        theme="AI power",
        trigger="load growth",
        region="global",
        as_of_date=date(2026, 8, 16),
        time_horizon_months=24,
        analysis_goal="unit",
        seed_products=(),
        seed_companies=(),
        research_mode="assisted",
        max_depth=1,
        max_nodes=10,
        max_iterations=1,
        max_sources=10,
        max_time_seconds=60,
        max_cost_usd=1,
        max_product_anchors=1,
    )
    draft = ProgressiveScorer().assess(
        node,
        tuple(claims_by_id.values()),
        tuple(cards_by_id.values()),
        request=request,
        contract_version="theme-chokepoint-scoring-v1.4",
    )

    class Provider:
        def search(self, **kwargs):
            completed = datetime(2026, 8, 16, tzinfo=timezone.utc)
            supply_ids = (
                by_dimension["effective_supply_concentration"].evidence_id,
                ambiguous.evidence_id,
            )
            route_specs = (
                ("demand", by_dimension["demand_pressure"].evidence_id),
                ("supply", supply_ids[0]),
                ("alternatives", by_dimension["substitute_weakness"].evidence_id),
            )
            return SegmentCriticReceipt(
                segment_id=node.node_id,
                protocol_version="segment-counter-search-v1.4",
                query_log_ids=tuple(f"query-{name}" for name, _ in route_specs),
                stop_reason="protocol_complete",
                unresolved_routes=(),
                demand_evidence_ids=(route_specs[0][1],),
                supply_evidence_ids=supply_ids,
                key_source_evidence_ids=(route_specs[0][1], supply_ids[0]),
                completed_at=completed,
                route_findings=tuple(
                    _bound_counter_route(
                        run_id=request.run_id,
                        route_id=name,
                        query=f"executed {name}",
                        query_log_id=f"query-{name}",
                        provider_request_receipt_id=f"provider-{name}",
                        status="supported" if name != "alternatives" else "explicit_negative",
                        evidence_ids=(evidence_id,),
                        finding=f"finding {name}",
                        executed_at=completed,
                        cost_usd=0.1,
                    )
                    for name, evidence_id in route_specs
                ),
                provider_request_receipt_ids=tuple(
                    f"provider-{name}" for name, _ in route_specs
                ),
                max_queries=3,
                max_time_seconds=60,
                max_cost_usd=1,
                cost_usd_spent=0.3,
                run_id=request.run_id,
                assessment_as_of=request.as_of_date.isoformat(),
            )

    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    criticized = EvidenceBoundSegmentCritic(
        _counter_provider(repository, include_ambiguous_supply=True)
    ).apply(
        node=node,
        ordinal_draft=draft,
        claims=tuple(claims_by_id.values()),
        evidence_cards=tuple(cards_by_id.values()),
        request=request,
    )

    assert criticized.independent_supply_constraint_count == 0


def test_stage3_consumes_materialized_current_route_counter_evidence(tmp_path):
    """SELECT INVARIANT: found counter material, not old positive cards, drives route gates."""
    from event_collector.theme_chokepoint.providers.critic import (
        EvidenceBoundSegmentCritic,
    )

    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)

    result = _controlled_loop(
        repository,
        RecordingAcquirer(_all_dimension_candidates()),
        ProgressiveScorer(),
        critic=EvidenceBoundSegmentCritic(
            _counter_provider(repository, found_routes={"demand", "supply"})
        ),
    ).run(request.run_id)

    assessment = result.assessments[0]
    receipt = assessment.critic_receipt
    assert receipt is not None
    found_routes = tuple(
        route for route in receipt.route_findings if route.status == "supported"
    )
    assert found_routes
    assert all(
        route.evidence_ids == route.new_counter_evidence_ids for route in found_routes
    )
    assert not {
        evidence_id for route in found_routes for evidence_id in route.evidence_ids
    }.intersection(card.evidence_id for card in result.evidence_cards)
    assert assessment.primary_state not in {
        "candidate_chokepoint",
        "strong_candidate_chokepoint",
    }


def test_segment_critic_collapses_relabelled_families_to_one_reconciled_event(
    tmp_path,
):
    """SELECT INVARIANT: controlled event provenance, not family labels, defines independence."""
    from event_collector.theme_chokepoint.providers.critic import (
        EvidenceBoundSegmentCritic,
    )

    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    candidates = _all_dimension_candidates()
    cards_by_id, claims_by_id, snapshots, fact_keys = {}, {}, {}, set()
    EvidenceChokepointLoop._materialize(
        candidates, cards_by_id, claims_by_id, snapshots, fact_keys
    )
    by_dimension = {
        card.primary_scoring_dimension: card for card in cards_by_id.values()
    }

    source_url = "https://controlled.example/shared-supply-event"
    source_text = "A controlled filing reports one shared supply constraint event."

    class ControlledResolutionClient:
        def resolve(self, **kwargs):
            raw_body = json.dumps(
                {
                    "observed_url": kwargs["source_url"],
                    "terminal_url": kwargs["source_url"],
                    "redirect_chain": [],
                    "relation": SourceRelation.ORIGINAL.value,
                    "origin_url": None,
                    "resolution_status": "verified",
                },
                sort_keys=True,
            ).encode("utf-8")
            return SourceResolutionRawResponse(
                provider="controlled-origin-resolver",
                provider_trace_id="resolver-shared-supply-event",
                http_status=200,
                raw_body=raw_body,
                retrieved_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
            )

    identity = repository.save_source_identity(
        CanonicalSourceIdentityResolver(ControlledResolutionClient()).resolve(source_url),
        canonical_publisher_id="publisher:controlled",
        canonical_document_id="document:shared-supply-event",
        origin_event_id="event:shared-supply-constraint",
    )
    version = repository.save_source_version(
        source_identity_id=identity.source_identity_id,
        retrieved_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        raw_bytes=source_text.encode("utf-8"),
        normalized_content=source_text,
        quote_span=(0, len(source_text), source_text),
        content_type="text/html",
        language="en",
        publication_time=datetime(2026, 8, 16, tzinfo=timezone.utc),
        updated_time=None,
    )
    for dimension, family in (
        ("effective_supply_concentration", "caller-family-one"),
        ("qualification_barrier", "caller-family-two"),
    ):
        card = by_dimension[dimension]
        cards_by_id[card.evidence_id] = replace(
            card,
            canonical_url=source_url,
            exact_quote=source_text,
            content_hash="sha256:" + sha256(source_text.encode("utf-8")).hexdigest(),
            evidence_family_id=family,
            source_identity_id=identity.source_identity_id,
            source_version_id=version.source_version_id,
            source_ambiguity=False,
        )

    node = next(
        item
        for item in repository.get_supply_chain_graph(request.run_id).nodes
        if item.depth
    )
    draft = ProgressiveScorer().assess(
        node,
        tuple(claims_by_id.values()),
        tuple(cards_by_id.values()),
        request=request,
        contract_version="theme-chokepoint-scoring-v1.4",
    )
    criticized = EvidenceBoundSegmentCritic(
        _counter_provider(repository, include_ambiguous_supply=True)
    ).apply(
        node=node,
        ordinal_draft=draft,
        claims=tuple(claims_by_id.values()),
        evidence_cards=tuple(cards_by_id.values()),
        request=request,
    )

    assert criticized.independent_supply_constraint_count == 1


def test_stage3_weak_self_reported_critic_receipt_cannot_unlock_candidate(tmp_path):
    """SELECT INVARIANT: arbitrary query IDs do not prove three executed routes."""
    from event_collector.theme_chokepoint.contracts import SegmentCriticReceipt
    from event_collector.theme_chokepoint.providers.critic import (
        EvidenceBoundSegmentCritic,
    )

    class WeakCounterSearch:
        def search(self, *, node, ordinal_draft, claims, evidence_cards, request):
            by_dimension = {
                card.primary_scoring_dimension: card for card in evidence_cards
            }
            return SegmentCriticReceipt(
                segment_id=node.node_id,
                protocol_version="segment-counter-search-v1.4",
                query_log_ids=("demand-query", "supply-query", "alternatives-query"),
                stop_reason="protocol_complete",
                unresolved_routes=(),
                demand_evidence_ids=(by_dimension["demand_pressure"].evidence_id,),
                supply_evidence_ids=(
                    by_dimension["effective_supply_concentration"].evidence_id,
                ),
                key_source_evidence_ids=(
                    by_dimension["demand_pressure"].evidence_id,
                ),
                completed_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
                run_id=request.run_id,
                assessment_as_of=request.as_of_date.isoformat(),
            )

    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    with pytest.raises(ValueError, match="executed counter-search routes"):
        _controlled_loop(
            repository,
            RecordingAcquirer(_all_dimension_candidates()),
            ProgressiveScorer(),
            critic=EvidenceBoundSegmentCritic(WeakCounterSearch()),
            expected_contract_sha256=(
                "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
            ),
            allow_unfrozen_overlay=True,
            expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
        ).run(request.run_id)


def test_stage3_deduplicates_one_origin_event_family_across_urls(tmp_path):
    """SELECT INVARIANT: repeated stories cannot become independent scoring evidence."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    duplicate = _candidate(url="https://mirror.example.net/copied-story")
    acquirer = RecordingAcquirer([*_all_dimension_candidates(), duplicate])

    result = _controlled_loop(repository, acquirer, ProgressiveScorer()).run(
        request.run_id
    )

    assert len(result.evidence_cards) == 6
    original = next(item for item in result.evidence_cards if item.claim_id == "claim-1")
    assert original.origin_event_id == "event-capacity-2026q2"
    assert original.evidence_family_id == "family-official-filing"


def test_stage3_preserves_distinct_atomic_facts_from_one_origin_family(tmp_path):
    """SELECT INVARIANT: source-family dedup never discards a different atomic fact."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    first = _candidate()
    second_quote = "Power-quality failures can block data-center commissioning."
    second = replace(
        first,
        article_id="article-2",
        canonical_url="https://example.com/report/criticality",
        location="Criticality section",
        quote_start=0,
        quote_end=len(second_quote),
        exact_quote=second_quote,
        original_text=second_quote,
        claim=replace(
            first.claim,
            claim_id="claim-2",
            statement=second_quote,
            material_field="downstream_criticality",
            primary_scoring_dimension="downstream_criticality",
            fact_key="power_quality_commissioning_block",
            condition_ids=("downstream_criticality.anchor_3.floor",),
        ),
    )

    result = _controlled_loop(
        repository,
        RecordingAcquirer([first, second]),
        ProgressiveScorer(),
        expected_contract_sha256=(
            "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
        ),
        allow_unfrozen_overlay=True,
        expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
    ).run(request.run_id)

    assert len(result.evidence_cards) == 2
    assert {claim.claim_id for claim in result.claims} == {"claim-1", "claim-2"}
    assert {claim.material_field for claim in result.claims} == {
        "effective_supply_concentration",
        "downstream_criticality",
    }


def test_stage3_same_source_span_has_one_atomic_identity_across_paraphrases(tmp_path):
    """SELECT INVARIANT: LLM wording cannot mint a second atomic fact identity."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    first = _candidate()
    second = replace(
        first,
        claim=replace(
            first.claim,
            claim_id="claim-paraphrase-2",
            statement="Qualified output expanded by only 5% year over year.",
            material_field="capacity_inelasticity",
            primary_scoring_dimension="capacity_inelasticity",
            fact_key="model-invented-second-key",
            condition_ids=("capacity_inelasticity.anchor_3.floor",),
        ),
    )

    assert stage3_module._atomic_fact_id(first) == stage3_module._atomic_fact_id(second)
    assert stage3_module._atomic_fact_id(first).startswith("atomic_fact_")
    with pytest.raises(ValueError, match="one primary high-score dimension"):
        _controlled_loop(
            repository,
            RecordingAcquirer([first, second]),
            ProgressiveScorer(),
            expected_contract_sha256=(
                "c7490b28fa12801c0a9e1aa6b054a676f2bb62265f5caac683d79e07d0491f03"
            ),
            allow_unfrozen_overlay=True,
            expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
        ).run(request.run_id)


def test_stage3_reprinted_source_span_has_one_atomic_identity_across_pages():
    """SELECT INVARIANT: a reprint page cannot mint a second atomic fact."""
    first = _candidate()
    second = replace(
        first,
        article_id="article-reprint-2",
        canonical_url="https://mirror.example.net/reprint-2",
        quote_start=17,
        quote_end=17 + len(first.exact_quote),
        original_text="Mirror navigation. " + first.exact_quote,
        claim=replace(first.claim, claim_id="claim-reprint-2"),
    )

    assert stage3_module._atomic_fact_id(first) == stage3_module._atomic_fact_id(second)


def test_stage3_persists_mechanical_relief_horizon_from_inventory_flow(tmp_path):
    """SELECT INVARIANT: Relief Horizon is calculated in the Stage 3 chain."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)

    class FixedReliefProvider:
        def build(self, *, node, claims, evidence_cards, request):
            periods = tuple(
                ReliefPeriodInput(
                    period_start=date(2026, month, 1),
                    period_end=date(2026, month + 1, 1) - timedelta(days=1),
                    inventory_begin=0,
                    route_outputs=(
                        ReliefRouteOutput(
                            route_id="qualified-route",
                            shared_pool_id=None,
                            nameplate=100,
                            yield_rate=1,
                            qualification_fraction=1,
                            target_scope_allocation=1,
                            availability=1,
                        ),
                    ),
                    scrap_or_unavailable=0,
                    firm_orders_due=100,
                    forecast=100,
                    forecast_includes_orders=True,
                    backlog_begin=0,
                    safety_stock_target=0,
                    required_fill_rate=1,
                    adjacent_constraint_active=False,
                )
                for month in (9, 10, 11)
            )
            return (
                ReliefScenarioInput("base", 30, "qualified_units_per_bucket", periods),
                ReliefScenarioInput("stress", 30, "qualified_units_per_bucket", periods),
            )

    result = _controlled_loop(
        repository,
        RecordingAcquirer([_candidate()]),
        ProgressiveScorer(),
        relief_provider=FixedReliefProvider(),
    ).run(request.run_id)

    assessment = result.assessments[0]
    assert assessment.relief_horizon == "base:2026-11-30;stress:2026-11-30"
    assert assessment.relief_assessment.base.node_relief_date == date(2026, 11, 30)
    assert assessment.relief_assessment.stress.system_relief_date == date(2026, 11, 30)
    assert repository.get_stage3_result(request.run_id) == result


def test_stage3_keeps_missing_evidence_unknown_and_returns_budget_incomplete(tmp_path):
    """SELECT INVARIANT: exhausted search never converts unknown [0,4] into zero."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository, request=_request(max_iterations=1, max_sources=1))

    result = _controlled_loop(
        repository, RecordingAcquirer([]), NeverResolvedScorer()
    ).run(request.run_id)

    assert result.status is RunStatus.INCOMPLETE_BUDGET_EXHAUSTED
    assert result.iterations_completed == 1
    assert result.incomplete_reasons == ("max_iterations:1",)
    assessment = result.assessments[0]
    assert assessment.score_min == 0.0
    assert assessment.score_max == 100.0
    assert assessment.primary_state is None
    assert all(
        dimension.evidence_state == "unknown"
        and (dimension.rating_min, dimension.rating_max) == (0, 4)
        for dimension in assessment.dimensions
    )
    persisted = repository.get_stage3_result(request.run_id)
    persisted_dimension = persisted.assessments[0].dimensions[0]
    assert persisted_dimension.rationale.startswith("No scoring evidence")
    assert persisted_dimension.missing_material_questions == (
        f"What evidence resolves {persisted_dimension.dimension}?",
    )


def test_stage3_rejects_malformed_original_span_without_partial_persistence(tmp_path):
    """SELECT INVARIANT: unverifiable quotes fail closed before ledger persistence."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    request = _seed_run(repository)
    bad = _candidate()
    bad = EvidenceCandidate(**{**bad.__dict__, "quote_start": 0, "quote_end": 4})

    with pytest.raises(ValueError, match="exact_quote does not match"):
        _controlled_loop(
            repository, RecordingAcquirer([bad]), NeverResolvedScorer()
        ).run(request.run_id)

    assert repository.list_evidence_cards(request.run_id) == ()
    assert repository.get_run(request.run_id).status is RunStatus.SUPPLY_CHAIN_GRAPH_READY
