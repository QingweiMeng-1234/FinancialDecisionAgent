from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from event_collector.theme_chokepoint.scoring_v15 import (
    CapacityTask,
    DownstreamImpactCohort,
    SubstituteRoute,
    SupplierSupply,
    combine_concentration_scores,
    score_capacity_inelasticity,
    score_demand_pressure,
    score_downstream_criticality,
    score_effective_supply_concentration,
    score_substitute_weakness,
    V15ScoringContract,
)
from event_collector.theme_chokepoint.contracts import (
    AssessmentScope,
    Claim,
    CompanyScope,
    EvidenceCard,
)
from event_collector.theme_chokepoint.evidence_reuse_v15 import (
    rebind_segment_evidence_to_company,
)
from event_collector.theme_chokepoint.validation_v15 import score_v15_segment_facts
from event_collector.theme_chokepoint.recompute_v15 import (
    build_segment_recompute_requests,
)
from types import SimpleNamespace
from datetime import date


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("growth", "expected"),
    [
        (-0.01, (0, 0)),
        (0.0, (0, 0)),
        (0.05, (1, 1)),
        (0.10, (2, 2)),
        (0.20, (3, 3)),
        (0.30, (3, 3)),
    ],
)
def test_demand_pressure_uses_canonical_annual_growth_ranges(growth, expected):
    """SELECT INVARIANT: v1.5 demand ranges use like-for-like annual growth."""
    result = score_demand_pressure(
        canonical_annual_growth=growth,
        direct_transmission_verified=True,
        inventory_verified=True,
    )
    assert (result.rating_min, result.rating_max) == expected


def test_demand_pressure_above_thirty_requires_transmission_and_inventory_proof():
    """SELECT INVARIANT: growth alone cannot produce exact Demand Pressure 4."""
    exact = score_demand_pressure(
        canonical_annual_growth=0.31,
        direct_transmission_verified=True,
        inventory_verified=True,
    )
    bounded = score_demand_pressure(
        canonical_annual_growth=0.31,
        direct_transmission_verified=True,
        inventory_verified=False,
    )

    assert (exact.rating_min, exact.rating_max) == (4, 4)
    assert (bounded.rating_min, bounded.rating_max) == (3, 4)


def test_demand_pressure_without_direct_transmission_has_no_positive_floor():
    """SELECT INVARIANT: annual growth outside the Segment cannot prove Segment demand."""
    mid = score_demand_pressure(
        canonical_annual_growth=0.15,
        direct_transmission_verified=False,
        inventory_verified=True,
    )
    high = score_demand_pressure(
        canonical_annual_growth=0.35,
        direct_transmission_verified=False,
        inventory_verified=True,
    )

    assert (mid.rating_min, mid.rating_max) == (0, 2)
    assert (high.rating_min, high.rating_max) == (0, 4)


def test_downstream_criticality_uses_structural_gate_then_operational_impact():
    blocked = score_downstream_criticality(
        structural_hard_block="pass",
        cohorts=(),
    )
    assert (blocked.rating_min, blocked.rating_max, blocked.bound_type) == (4, 4, "exact")

    partial = score_downstream_criticality(
        structural_hard_block="fail",
        cohorts=(DownstreamImpactCohort(delay_months=2, impact_ratio=0.08),),
    )
    assert (partial.rating_min, partial.rating_max) == (2, 2)

    unresolved_structure = score_downstream_criticality(
        structural_hard_block="unknown",
        cohorts=(DownstreamImpactCohort(delay_months=2, impact_ratio=0.08),),
    )
    assert (unresolved_structure.rating_min, unresolved_structure.rating_max) == (2, 4)


def test_downstream_temporary_stop_cannot_trigger_four_and_inventory_can_prove_zero():
    temporary = score_downstream_criticality(
        structural_hard_block="fail",
        cohorts=(DownstreamImpactCohort(delay_months=5, impact_ratio=0.20),),
    )
    assert (temporary.rating_min, temporary.rating_max) == (3, 3)

    absorbed = score_downstream_criticality(
        structural_hard_block="fail",
        cohorts=(),
        explicit_no_impact=True,
    )
    assert (absorbed.rating_min, absorbed.rating_max) == (0, 0)


def test_concentration_uses_30_30_40_half_up_and_failover_excludes_baseline():
    result = score_effective_supply_concentration(
        suppliers=(
            SupplierSupply("a", 80, 1, 1, 1, 1, additional_qualified_output_90d=0),
            SupplierSupply("b", 20, 1, 1, 1, 1, additional_qualified_output_90d=5),
        ),
        target_demand=100,
    )

    assert result.largest_effective_share == 0.8
    assert result.qualified_failover_ratio == 0.05
    assert result.component_scores == (3, 4, 3)
    assert (result.rating.rating_min, result.rating.rating_max) == (3, 3)

    assert combine_concentration_scores((0, 0, 1)).rating_min == 0
    assert combine_concentration_scores((0, 1, 1)).rating_min == 1


def test_concentration_hard_gate_and_missing_component_bounds():
    monopoly = score_effective_supply_concentration(
        suppliers=(SupplierSupply("a", 100, 1, 1, 1, 1),),
        target_demand=100,
    )
    assert monopoly.component_scores == (4, 4, 4)
    assert (monopoly.rating.rating_min, monopoly.rating.rating_max) == (4, 4)

    bounded = combine_concentration_scores((2, None, 3))
    assert bounded.rating_min < bounded.rating_max
    assert 0 <= bounded.rating_min <= bounded.rating_max <= 4


def test_capacity_uses_residual_gap_critical_path_and_three_stable_months():
    tasks = (
        CapacityTask("facility", 12, (), "facility"),
        CapacityTask("equipment", 10, (), "equipment"),
        CapacityTask("install", 3, ("facility", "equipment"), "equipment"),
        CapacityTask("yield", 3, ("install",), "yield"),
    )
    result = score_capacity_inelasticity(
        top1_loss_output=60,
        failover_output_90d=10,
        stable_incremental_output=50,
        tasks=tasks,
    )
    assert result.required_increment == 50
    assert result.critical_path_months == 18
    assert result.stable_output_months == 21
    assert (result.rating.rating_min, result.rating.rating_max) == (4, 4)


def test_capacity_eighteen_months_with_one_constraint_is_three_to_four():
    result = score_capacity_inelasticity(
        top1_loss_output=30,
        failover_output_90d=0,
        stable_incremental_output=30,
        tasks=(CapacityTask("single", 15, (), "equipment"),),
    )
    assert result.stable_output_months == 18
    assert result.constraint_classes == ("equipment",)
    assert (result.rating.rating_min, result.rating.rating_max) == (3, 4)

    no_gap = score_capacity_inelasticity(
        top1_loss_output=30,
        failover_output_90d=30,
        stable_incremental_output=0,
        tasks=(),
    )
    assert (no_gap.rating.rating_min, no_gap.rating.rating_max) == (0, 0)


def test_substitute_coverage_uses_total_demand_and_deduplicates_shared_pool():
    result = score_substitute_weakness(
        routes=(
            SubstituteRoute("a", "production", 20, "shared-line"),
            SubstituteRoute("b", "production", 15, "shared-line"),
            SubstituteRoute("c", "ready", 12, "independent-line"),
        ),
        target_demand=100,
        discovery_rounds_without_new_routes=2,
        required_categories=("product", "process"),
        searched_categories=("product", "process"),
    )
    assert result.ready_coverage == 0.32
    assert (result.rating.rating_min, result.rating.rating_max) == (0, 0)


def test_substitute_exact_four_requires_convergence_and_no_unresolved_route():
    negative = score_substitute_weakness(
        routes=(
            SubstituteRoute("a", "explicit_failure", 0, category="product"),
            SubstituteRoute("b", "insufficient_capacity", 0, category="process"),
        ),
        target_demand=100,
        discovery_rounds_without_new_routes=2,
        required_categories=("product", "process"),
        searched_categories=("product", "process"),
    )
    assert negative.protocol_complete is True
    assert (negative.rating.rating_min, negative.rating.rating_max) == (4, 4)

    unresolved = score_substitute_weakness(
        routes=(SubstituteRoute("a", "unresolved", 0, category="product"),),
        target_demand=100,
        discovery_rounds_without_new_routes=2,
        required_categories=("product",),
        searched_categories=("product",),
    )
    assert unresolved.protocol_complete is False
    assert (unresolved.rating.rating_min, unresolved.rating.rating_max) == (0, 4)

    exhausted = score_substitute_weakness(
        routes=(),
        target_demand=100,
        discovery_rounds_without_new_routes=1,
        required_categories=("product",),
        searched_categories=("product",),
        budget_exhausted=True,
    )
    assert exhausted.protocol_complete is False
    assert (exhausted.rating.rating_min, exhausted.rating.rating_max) == (0, 4)


def test_substitute_incomplete_search_keeps_known_coverage_as_an_upper_bound():
    """SELECT INVARIANT: incomplete discovery cannot turn known coverage into an exact score."""
    partial = score_substitute_weakness(
        routes=(SubstituteRoute("a", "production", 20),),
        target_demand=100,
        discovery_rounds_without_new_routes=1,
        required_categories=("product",),
        searched_categories=("product",),
    )
    prototype = score_substitute_weakness(
        routes=(SubstituteRoute("p", "prototype", 0),),
        target_demand=100,
        discovery_rounds_without_new_routes=1,
        required_categories=("product",),
        searched_categories=("product",),
    )

    assert partial.protocol_complete is False
    assert (partial.rating.rating_min, partial.rating.rating_max) == (0, 1)
    assert (prototype.rating.rating_min, prototype.rating.rating_max) == (0, 3)


def test_substitute_empty_standard_category_registry_cannot_produce_four():
    """SELECT INVARIANT: vacuous category coverage cannot prove no substitutes exist."""
    with pytest.raises(ValueError, match="required route categories"):
        score_substitute_weakness(
            routes=(),
            target_demand=100,
            discovery_rounds_without_new_routes=2,
            required_categories=(),
            searched_categories=(),
        )


def test_v15_machine_contract_is_hash_pinned_and_declares_new_semantics():
    path = ROOT / "tools" / "theme-chokepoint" / "semantic-task-contract-v1.5.json"
    expected = sha256(path.read_bytes()).hexdigest()
    contract = V15ScoringContract(path, expected_sha256=expected)

    assert contract.version == "theme-chokepoint-scoring-v1.5"
    assert contract.status == "freeze_candidate"
    assert contract.segment_weights == {
        "demand_pressure": 15,
        "downstream_criticality": 20,
        "effective_supply_concentration": 15,
        "qualification_barrier": 15,
        "capacity_inelasticity": 15,
        "substitute_weakness": 20,
    }
    assert contract.segment_semantics["downstream_criticality"]["operational_shock_days"] == 90
    assert contract.segment_semantics["effective_supply_concentration"]["weights"] == [0.3, 0.3, 0.4]
    assert contract.segment_semantics["capacity_inelasticity"]["stable_months_required"] == 3
    assert contract.segment_semantics["substitute_weakness"]["convergence_rounds"] == 2

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        V15ScoringContract(path, expected_sha256="0" * 64)


def test_stage3_company_subject_is_rebound_before_company_scoring():
    segment_scope = AssessmentScope(
        company_id=None,
        product_id="hbm3e",
        segment_id="hbm",
        customer_or_platform_scope="accelerator-x",
        geography="global",
        time_horizon_months=24,
        as_of_date=date(2026, 8, 20),
    )
    claim = Claim(
        claim_id="segment-claim",
        node_id="hbm",
        claim_type="source_fact",
        statement="Supplier A provides qualified HBM3E output.",
        material_field="effective_supply_concentration",
        primary_scoring_dimension="effective_supply_concentration",
        scoring_use="primary",
        evidence_ids=("segment-evidence",),
        assessment_scope=segment_scope,
        subject_company_ids=("supplier-a",),
    )
    card = EvidenceCard(
        evidence_id="segment-evidence",
        claim_id="segment-claim",
        article_id="article-1",
        canonical_url="https://example.com/a",
        source_title="Primary source",
        publisher="Example",
        source_type="company_filing",
        publication_date=date(2026, 8, 1),
        data_as_of_date=date(2026, 8, 1),
        location="p.1",
        quote_start=0,
        quote_end=12,
        exact_quote="Supplier A.",
        content_hash="sha256:abc",
        stance="supports",
        limitations="same scope",
        extraction_model="extractor",
        prompt_version="v1.5",
        origin_event_id="event-1",
        evidence_family_id="family-1",
        assessment_scope=segment_scope,
        subject_company_ids=("supplier-a",),
        source_identity_id="source-identity-1",
        source_version_id="source-version-1",
    )
    company_scope = CompanyScope(
        company_id="supplier-a",
        segment_id="hbm",
        product_id="hbm3e",
        customer_or_platform_scope="accelerator-x",
        geography="global",
        time_horizon_months=24,
        as_of_date=date(2026, 8, 20),
    )

    rebound_claim, rebound_card = rebind_segment_evidence_to_company(
        claim=claim,
        card=card,
        company_scope=company_scope,
        company_dimension="qualified_effective_capacity",
    )

    assert rebound_claim.assessment_scope.company_id == "supplier-a"
    assert rebound_claim.derived_from_evidence_id == "segment-evidence"
    assert rebound_card.derived_from_evidence_id == "segment-evidence"
    assert rebound_card.source_identity_id == card.source_identity_id
    assert rebound_card.source_version_id == card.source_version_id
    assert rebound_card.exact_quote == card.exact_quote
    assert rebound_claim.claim_id != claim.claim_id
    assert rebound_card.evidence_id != card.evidence_id


def test_rebinding_rejects_unattributed_or_cross_scope_segment_evidence():
    scope = AssessmentScope(None, "p", "s", "platform", "global", 12, date(2026, 8, 20))
    claim = Claim(
        "c", "s", "source_fact", "fact", "field", "field", "primary", ("e",),
        assessment_scope=scope,
    )
    card = EvidenceCard(
        "e", "c", "a", "https://example.com", "t", "p", "filing", None, None,
        "l", 0, 1, "x", "sha256:x", "supports", "none", "m", "v", "o", "f",
        assessment_scope=scope,
    )
    company_scope = CompanyScope("other", "s", "p", "platform", "global", 12, date(2026, 8, 20))
    with pytest.raises(ValueError, match="subject company"):
        rebind_segment_evidence_to_company(
            claim=claim,
            card=card,
            company_scope=company_scope,
            company_dimension="qualified_effective_capacity",
        )


def test_v15_fact_validator_mechanically_scores_model_extracted_facts():
    """SELECT INVARIANT: the v1.5 model supplies facts; code owns all changed ordinals."""
    payload = {
        "schema_version": "theme-chokepoint-segment-facts-v1.5",
        "demand_pressure": {
            "canonical_annual_growth": 0.15,
            "direct_transmission_verified": True,
            "inventory_verified": True,
        },
        "downstream_criticality": {
            "structural_hard_block": "fail",
            "explicit_no_impact": False,
            "cohorts": [{"delay_months": 2, "impact_ratio": 0.08}],
        },
        "effective_supply_concentration": {
            "target_demand": 100,
            "suppliers": [
                {
                    "supplier_id": "a",
                    "nameplate_output": 80,
                    "yield_fraction": 1,
                    "qualification_fraction": 1,
                    "target_scope_allocation_fraction": 1,
                    "availability_fraction": 1,
                    "additional_qualified_output_90d": 0,
                },
                {
                    "supplier_id": "b",
                    "nameplate_output": 20,
                    "yield_fraction": 1,
                    "qualification_fraction": 1,
                    "target_scope_allocation_fraction": 1,
                    "availability_fraction": 1,
                    "additional_qualified_output_90d": 5,
                },
            ],
        },
        "qualification_barrier": {"rating_min": 2, "rating_max": 2},
        "capacity_inelasticity": {
            "top1_loss_output": 80,
            "failover_output_90d": 5,
            "stable_incremental_output": 75,
            "tasks": [
                {
                    "task_id": "equipment",
                    "duration_months": 12,
                    "dependencies": [],
                    "constraint_class": "equipment",
                }
            ],
        },
        "substitute_weakness": {
            "target_demand": 100,
            "discovery_rounds_without_new_routes": 2,
            "required_categories": ["product"],
            "searched_categories": ["product"],
            "routes": [
                {
                    "route_id": "alt",
                    "status": "production",
                    "qualified_output": 20,
                    "capacity_pool_id": "alt-line",
                    "category": "product",
                }
            ],
        },
    }

    scored = score_v15_segment_facts(payload)

    assert scored["demand_pressure"] == (2, 2)
    assert scored["downstream_criticality"] == (2, 2)
    assert scored["effective_supply_concentration"] == (3, 3)
    assert scored["qualification_barrier"] == (2, 2)
    assert scored["capacity_inelasticity"] == (3, 3)
    assert scored["substitute_weakness"] == (1, 1)


def test_v15_fact_validator_rejects_wrong_schema_and_model_authored_scores():
    """SELECT INVARIANT: a model cannot bypass v1.5 mechanical scoring with final ratings."""
    with pytest.raises(ValueError, match="schema version"):
        score_v15_segment_facts({"schema_version": "v1.4"})
    with pytest.raises(ValueError, match="model-authored rating"):
        score_v15_segment_facts(
            {
                "schema_version": "theme-chokepoint-segment-facts-v1.5",
                "rating": 4,
            }
        )


def test_stage4_segment_fact_creates_recompute_request_without_mutating_stage3():
    """SELECT INVARIANT: Stage 4 discoveries request a new canonical Stage 3 version."""
    scope = AssessmentScope(
        "supplier-a", "hbm3e", "hbm", "accelerator-x", "global", 24,
        date(2026, 8, 20),
    )
    claim = Claim(
        claim_id="company-capacity-claim",
        node_id="hbm",
        claim_type="source_fact",
        statement="New qualified output changed effective supply.",
        material_field="effective_supply_concentration",
        primary_scoring_dimension="effective_supply_concentration",
        scoring_use="primary",
        evidence_ids=("company-capacity-evidence",),
        assessment_scope=scope,
    )
    stage3 = SimpleNamespace(
        executable_contract_id="v1.5-machine",
        executable_contract_sha256="a" * 64,
        assessments=(SimpleNamespace(segment_id="hbm", score_min=60),),
    )

    requests = build_segment_recompute_requests(
        run_id="run-1",
        stage3_result=stage3,
        company_claims=(claim,),
    )

    assert len(requests) == 1
    assert requests[0].segment_id == "hbm"
    assert requests[0].triggering_claim_ids == (claim.claim_id,)
    assert requests[0].triggering_evidence_ids == claim.evidence_ids
    assert requests[0].source_stage3_contract_sha256 == "a" * 64
    assert stage3.assessments[0].score_min == 60
