from __future__ import annotations

from datetime import date

import pytest

from event_collector.theme_chokepoint.contracts import (
    DemandFrame,
    ProductAnchorDraft,
    ResearchRequest,
    RunStatus,
)
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.stage1 import (
    AssistedGateClosed,
    AssistedThemeFramingService,
)


def request(**overrides) -> ResearchRequest:
    values = {
        "run_id": "run-stage1-001",
        "theme": "AI data-center power infrastructure",
        "trigger": "AI compute deployment growth",
        "region": "global",
        "as_of_date": date(2026, 8, 16),
        "time_horizon_months": 24,
        "analysis_goal": "identify upstream chokepoints and potential beneficiaries",
        "seed_products": (),
        "seed_companies": (),
        "research_mode": "assisted",
        "max_depth": 3,
        "max_nodes": 30,
        "max_iterations": 3,
        "max_sources": 40,
        "max_time_seconds": 900,
        "max_cost_usd": 10.0,
        "max_product_anchors": 2,
    }
    values.update(overrides)
    return ResearchRequest(**values)


def clear_frame() -> DemandFrame:
    return DemandFrame(
        normalized_theme="ai data-center power infrastructure",
        scope="Global AI data-center electrical infrastructure over 24 months",
        exclusions=("general utility generation",),
        demand_hypothesis=(
            "Growth in deployed AI compute increases demand for data-center power-distribution equipment."
        ),
        measurable_demand_variables=("AI accelerator deployments", "data-center MW commissioned"),
        time_horizon_months=24,
        unresolved_questions=(),
    )


def anchor(product: str, confidence: float) -> ProductAnchorDraft:
    return ProductAnchorDraft(
        product_name=product,
        buyer_or_user="hyperscale data-center operator",
        demand_variable="data-center MW commissioned",
        theme_link=(
            f"More commissioned AI data-center capacity requires additional {product}."
        ),
        confidence=confidence,
        supporting_evidence_ids=("ev-001",),
        missing_evidence=("qualified supply denominator",),
    )


class RecordingFramer:
    def __init__(self, repository, frame):
        self.repository = repository
        self.frame_result = frame
        self.calls = 0

    def frame(self, research_request):
        self.calls += 1
        persisted = self.repository.get_run(research_request.run_id)
        assert persisted.status is RunStatus.REQUEST_STORED
        assert persisted.request.theme == research_request.theme
        return self.frame_result


class RecordingProposer:
    def __init__(self, drafts):
        self.drafts = drafts
        self.calls = 0

    def propose(self, research_request, demand_frame):
        self.calls += 1
        return list(self.drafts)


def test_stage1_persists_request_before_framing_and_stops_for_clarification(tmp_path):
    """SELECT INVARIANT: unclear framing stops before product or supply-chain work."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    unclear = DemandFrame(
        normalized_theme="ai",
        scope="global",
        exclusions=(),
        demand_hypothesis=None,
        measurable_demand_variables=(),
        time_horizon_months=24,
        unresolved_questions=("Which demand shock should be tested?",),
    )
    framer = RecordingFramer(repository, unclear)
    proposer = RecordingProposer([anchor("large power transformers", 0.8)])

    result = AssistedThemeFramingService(repository, framer, proposer).start(request())

    assert framer.calls == 1
    assert proposer.calls == 0
    assert result.status is RunStatus.NEEDS_CLARIFICATION
    assert result.demand_frame == unclear
    assert result.product_anchors == ()
    assert repository.get_run(result.run_id).status is RunStatus.NEEDS_CLARIFICATION


def test_stage1_bounds_product_proposals_and_waits_for_human_confirmation(tmp_path):
    """SELECT INVARIANT: a clear frame creates a bounded review list, not an open gate."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    framer = RecordingFramer(repository, clear_frame())
    proposer = RecordingProposer(
        [
            anchor("large power transformers", 0.91),
            anchor("UPS and switchgear", 0.84),
            anchor("liquid cooling distribution units", 0.79),
        ]
    )
    service = AssistedThemeFramingService(repository, framer, proposer)

    result = service.start(request(max_product_anchors=2))

    assert result.status is RunStatus.AWAITING_PRODUCT_CONFIRMATION
    assert [item.product_name for item in result.product_anchors] == [
        "large power transformers",
        "UPS and switchgear",
    ]
    assert all(item.status == "proposed" for item in result.product_anchors)
    with pytest.raises(AssistedGateClosed, match="product anchor confirmation"):
        service.confirmed_anchors_for_supply_chain(result.run_id)


def test_stage1_persists_human_confirmation_and_opens_supply_chain_gate(tmp_path):
    """SELECT INVARIANT: Stage 2 opens only from a durable confirmed-anchor receipt."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    service = AssistedThemeFramingService(
        repository,
        RecordingFramer(repository, clear_frame()),
        RecordingProposer([anchor("large power transformers", 0.91), anchor("UPS", 0.8)]),
    )
    result = service.start(request())
    selected = result.product_anchors[0]

    receipt = service.confirm_product_anchors(
        result.run_id,
        anchor_ids=(selected.anchor_id,),
        confirmed_by="product-owner",
    )
    reloaded = repository.get_run(result.run_id)

    assert receipt.status is RunStatus.READY_FOR_SUPPLY_CHAIN
    assert receipt.confirmed_anchor_ids == (selected.anchor_id,)
    assert receipt.confirmed_by == "product-owner"
    assert receipt.confirmed_at is not None
    assert reloaded.status is RunStatus.READY_FOR_SUPPLY_CHAIN
    assert reloaded.confirmed_by == "product-owner"
    confirmed = service.confirmed_anchors_for_supply_chain(result.run_id)
    assert tuple(item.anchor_id for item in confirmed) == (selected.anchor_id,)
    assert tuple(item.status for item in confirmed) == ("confirmed",)


def test_stage1_rejects_empty_or_unknown_confirmation_without_opening_gate(tmp_path):
    """SELECT INVARIANT: malformed approval cannot create a false assisted-gate receipt."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    service = AssistedThemeFramingService(
        repository,
        RecordingFramer(repository, clear_frame()),
        RecordingProposer([anchor("large power transformers", 0.91)]),
    )
    result = service.start(request())

    with pytest.raises(ValueError, match="at least one"):
        service.confirm_product_anchors(result.run_id, anchor_ids=(), confirmed_by="owner")
    with pytest.raises(ValueError, match="not proposed"):
        service.confirm_product_anchors(
            result.run_id, anchor_ids=("anchor-does-not-exist",), confirmed_by="owner"
        )

    assert repository.get_run(result.run_id).status is RunStatus.AWAITING_PRODUCT_CONFIRMATION


def test_stage1_rejects_non_assisted_mode_and_invalid_budget_before_persistence(tmp_path):
    """SELECT INVARIANT: Stage 1 stores only valid assisted-MVP requests."""
    repository = ThemeChokepointRepository(tmp_path / "theme.db")
    service = AssistedThemeFramingService(
        repository,
        RecordingFramer(repository, clear_frame()),
        RecordingProposer([]),
    )

    with pytest.raises(ValueError, match="research_mode"):
        service.start(request(run_id="bad-mode", research_mode="autonomous"))
    with pytest.raises(ValueError, match="max_product_anchors"):
        service.start(request(run_id="bad-budget", max_product_anchors=0))

    assert repository.list_run_ids() == ()
