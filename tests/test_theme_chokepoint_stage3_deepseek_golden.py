from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from event_collector.theme_chokepoint.contracts import (
    ClaimDraft,
    DemandFrame,
    DependencyEdge,
    EvidenceCandidate,
    ProductAnchor,
    ResearchRequest,
    RunStatus,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.providers import DeepSeekV14SegmentScorer
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.stage3 import EvidenceChokepointLoop


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PACK = (
    ROOT
    / "docs"
    / "product"
    / "ai-datacenter-power-cooling-golden"
    / "evidence-pack-v0.1.json"
)
GOLDEN_EVIDENCE_BY_FIELD = {
    "demand_pressure": (("E01", "primary"),),
    "downstream_criticality": (("E25", "primary"),),
    "capacity_inelasticity": (("E03", "primary"), ("E24", "floor_only")),
}


class FixedGoldenAcquirer:
    def __init__(self, node_id: str):
        payload = json.loads(GOLDEN_PACK.read_text(encoding="utf-8"))
        self.items = {item["evidence_id"]: item for item in payload["items"]}
        self.node_id = node_id
        self.returned_fields = set()

    def acquire(self, *, query, run, node, material_field):
        if material_field in self.returned_fields:
            return []
        self.returned_fields.add(material_field)
        return [
            self._candidate(evidence_id, material_field, scoring_use)
            for evidence_id, scoring_use in GOLDEN_EVIDENCE_BY_FIELD.get(
                material_field, ()
            )
        ]

    def _candidate(self, evidence_id, material_field, scoring_use):
        item = self.items[evidence_id]
        quote = item["exact_quote"]
        return EvidenceCandidate(
            article_id=f"golden-{evidence_id.casefold()}",
            canonical_url=item["url"],
            source_title=f"{item['publisher']} fixed Golden evidence {evidence_id}",
            publisher=item["publisher"],
            source_type=item["source_type"],
            publication_date=date.fromisoformat(item["publication_date"]),
            data_as_of_date=date.fromisoformat(item["data_as_of"]),
            location=item["location"],
            quote_start=0,
            quote_end=len(quote),
            exact_quote=quote,
            original_text=quote,
            source_mode="original_text",
            stance="supports",
            limitations=item["limitations"],
            extraction_model="golden-human-v0.1",
            prompt_version="fixed-golden-ledger-v0.1",
            origin_event_id=item["origin_event_id"],
            evidence_family_id=item["evidence_family_id"],
            claim=ClaimDraft(
                claim_id=f"claim-golden-{evidence_id.casefold()}",
                node_id=self.node_id,
                claim_type="source_fact",
                statement=quote,
                material_field=material_field,
                primary_scoring_dimension=(
                    material_field if scoring_use == "primary" else None
                ),
                scoring_use=scoring_use,
            ),
        )


def _seed_power_transformer_run(repository):
    request = ResearchRequest(
        run_id="golden-pwr-c01-deepseek",
        theme="AI data-center power equipment",
        trigger="AI data-center electricity demand growth",
        region="US",
        as_of_date=date(2026, 8, 15),
        time_horizon_months=24,
        analysis_goal="audit the large-transformer chokepoint with fixed Golden evidence",
        seed_products=("US three-phase transformers",),
        seed_companies=("Eaton",),
        research_mode="golden_regression",
        max_depth=2,
        max_nodes=4,
        max_iterations=1,
        max_sources=5,
        max_time_seconds=300,
        max_cost_usd=2.0,
        max_product_anchors=1,
    )
    repository.create_request(request)
    anchor = ProductAnchor(
        anchor_id="golden-anchor-transformer",
        product_name="US three-phase transformer",
        buyer_or_user="US utilities and data-center power projects",
        demand_variable="qualified saleable transformer output",
        theme_link="AI data-center interconnection requires transformer capacity.",
        confidence=1.0,
        supporting_evidence_ids=(),
        missing_evidence=(),
        status="proposed",
    )
    repository.save_framing(
        request.run_id,
        DemandFrame(
            normalized_theme=request.theme,
            scope="US, 24 months, as of 2026-08-15",
            exclusions=(),
            demand_hypothesis="Data-center load growth tightens transformer availability.",
            measurable_demand_variables=("orders", "lead time", "saleable output"),
            time_horizon_months=24,
            unresolved_questions=(),
        ),
        status=RunStatus.AWAITING_PRODUCT_CONFIRMATION,
        anchors=(anchor,),
    )
    repository.confirm_product_anchors(
        request.run_id, (anchor.anchor_id,), confirmed_by="golden-regression"
    )
    product = SupplyChainNode(
        node_id="golden-product-transformer",
        normalized_name=anchor.product_name,
        node_type="product",
        depth=0,
        status="confirmed",
        description="PWR-C01 fixed Golden product anchor.",
        aliases=(),
        product_anchor_id=anchor.anchor_id,
    )
    segment = SupplyChainNode(
        node_id="golden-segment-large-transformer",
        normalized_name="US large power transformer and switchgear supply",
        node_type="product_segment",
        depth=1,
        status="proposed",
        description="PWR-C01 segment scope for fixed-evidence Stage 3 regression.",
        aliases=("large power transformer",),
    )
    repository.save_supply_chain_graph(
        request.run_id,
        nodes=(product, segment),
        edges=(
            DependencyEdge(
                edge_id="golden-edge-transformer",
                downstream_node_id=product.node_id,
                upstream_node_id=segment.node_id,
                relation_type="qualified_supply",
                demand_transmission="Data-center power demand raises transformer demand.",
                criticality_hypothesis="Long lead times can delay power availability.",
                substitute_hypothesis="Like-for-like qualified failover is not evidenced.",
                confidence=1.0,
                supporting_claim_ids=(),
                verification_questions=(),
                status="proposed",
            ),
        ),
        truncation_reasons=(),
    )
    return request, segment


@pytest.mark.skipif(
    os.getenv("RUN_DEEPSEEK_GOLDEN") != "1",
    reason="set RUN_DEEPSEEK_GOLDEN=1 for the paid live DeepSeek regression",
)
def test_real_stage3_deepseek_preserves_pwr_c01_golden_evidence_boundaries(tmp_path):
    """Live regression: fixed Golden evidence may not become invented certainty."""
    load_dotenv(ROOT / ".env", override=False)
    repository = ThemeChokepointRepository(tmp_path / "golden-stage3.db")
    request, segment = _seed_power_transformer_run(repository)

    result = EvidenceChokepointLoop(
        repository,
        FixedGoldenAcquirer(segment.node_id),
        DeepSeekV14SegmentScorer(),
    ).run(request.run_id)

    assert result.status is RunStatus.INCOMPLETE_BUDGET_EXHAUSTED
    assert len(result.evidence_cards) == 4
    assert {card.origin_event_id for card in result.evidence_cards} == {
        "eaton-q4-2025-results",
        "eaton-jonesville-transformer-investment-2025",
        "nerc-risc-risk-priorities-2025",
    }
    assessment = result.assessments[0]
    assert assessment.primary_state is None
    by_name = {item.dimension: item for item in assessment.dimensions}
    for name in (
        "effective_supply_concentration",
        "qualification_barrier",
        "substitute_weakness",
    ):
        assert by_name[name].evidence_state == "unknown"
        assert by_name[name].bound_type == "none"
        assert (by_name[name].rating_min, by_name[name].rating_max) == (0, 4)
    assert not any(
        item.bound_type == "exact" and item.rating_max == 0
        for item in assessment.dimensions
    )
    assert not (
        by_name["capacity_inelasticity"].bound_type == "exact"
        and by_name["capacity_inelasticity"].rating_min == 4
    )
    assert all(item.rationale for item in assessment.dimensions)
    assert repository.get_stage3_result(request.run_id) == result
