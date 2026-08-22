from __future__ import annotations

from dataclasses import replace
from datetime import date
import json

import pytest

from event_collector.theme_chokepoint.contracts import (
    Claim,
    EvidenceCard,
    ResearchRequest,
    SupplyChainNode,
)
from event_collector.theme_chokepoint.providers.scorer import (
    DeepSeekV14SegmentScorer,
    SEGMENT_DIMENSIONS,
)


class NoCallCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("LLM must not be called without scoring evidence")


class NoCallClient:
    def __init__(self):
        self.chat = type("Chat", (), {})()
        self.chat.completions = NoCallCompletions()


class FakeCompletions:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = type("Message", (), {"content": json.dumps(self.payload)})()
        choice = type("Choice", (), {"message": message})()
        return type("Completion", (), {"choices": [choice]})()


class FakeClient:
    def __init__(self, payload):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(payload)


class SequencedCompletions:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = next(self.payloads)
        content = payload if isinstance(payload, str) else json.dumps(payload)
        message = type("Message", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        return type("Completion", (), {"choices": [choice]})()


class SequencedClient:
    def __init__(self, payloads):
        self.chat = type("Chat", (), {})()
        self.chat.completions = SequencedCompletions(payloads)


def _node():
    return SupplyChainNode(
        node_id="segment-critical-power",
        normalized_name="AI data center critical power distribution",
        node_type="product_segment",
        depth=1,
        status="proposed",
        description="UPS, PDU and busway for AI data centers.",
        aliases=("critical power",),
    )


def _request():
    return ResearchRequest(
        run_id="run-segment-scorer",
        theme="AI data-center power infrastructure",
        trigger="AI compute deployment growth",
        region="US",
        as_of_date=date(2026, 8, 16),
        time_horizon_months=24,
        analysis_goal="identify evidence-bounded chokepoints",
        seed_products=(),
        seed_companies=(),
        research_mode="assisted",
        max_depth=2,
        max_nodes=10,
        max_iterations=2,
        max_sources=10,
        max_time_seconds=600,
        max_cost_usd=5,
        max_product_anchors=2,
    )


def _claim_and_card(
    *,
    dimension="demand_pressure",
    scoring_use="primary",
    stance="supports",
    claim_capabilities=("general_scoring_evidence", "direct_upper_bound"),
):
    quote = "Orders for the same product increased 7% year over year."
    claim = Claim(
        claim_id="claim-demand-1",
        node_id=_node().node_id,
        claim_type="source_fact",
        statement=quote,
        material_field=dimension,
        primary_scoring_dimension=dimension if scoring_use == "primary" else None,
        scoring_use=scoring_use,
        evidence_ids=("evidence-demand-1",),
        claim_capabilities=tuple(claim_capabilities),
    )
    card = EvidenceCard(
        evidence_id="evidence-demand-1",
        claim_id=claim.claim_id,
        article_id="article-demand-1",
        canonical_url="https://issuer.example.com/demand-report",
        source_title="Official demand report",
        publisher="issuer.example.com",
        source_type="original_web",
        publication_date=date(2026, 8, 1),
        data_as_of_date=date(2026, 6, 30),
        location="Orders section",
        quote_start=0,
        quote_end=len(quote),
        exact_quote=quote,
        content_hash="sha256:" + "a" * 64,
        stance=stance,
        limitations="Company-reported; exact product scope is stated.",
        extraction_model="deepseek-chat",
        prompt_version="evidence-span-v1",
        origin_event_id="event-demand-1",
        evidence_family_id="family-demand-1",
        claim_capabilities=tuple(claim_capabilities),
    )
    return claim, card


def _unknown_payload(name):
    return {
        "dimension": name,
        "rating_min": 0,
        "rating_max": 4,
        "evidence_state": "unknown",
        "bound_type": "none",
        "bound_basis": {
            "floor_anchor": None,
            "ceiling_anchor": None,
            "exact_basis": None,
            "unresolved_higher_anchors": [],
            "excluded_higher_anchors": [],
        },
        "evidence_ids": [],
        "stale": False,
        "rationale": "No complete anchor predicate is supported.",
        "missing_material_questions": [f"What evidence resolves {name}?"],
    }


def _demand_exact_two_payload():
    dimensions = [_unknown_payload(name) for name in SEGMENT_DIMENSIONS]
    dimensions[0] = {
        "dimension": "demand_pressure",
        "rating_min": 2,
        "rating_max": 2,
        "evidence_state": "supported",
        "bound_type": "exact",
        "bound_basis": {
            "floor_anchor": 2,
            "ceiling_anchor": 2,
            "exact_basis": "contract_exclusivity",
            "unresolved_higher_anchors": [],
            "excluded_higher_anchors": [3, 4],
        },
        "evidence_ids": ["evidence-demand-1"],
        "stale": False,
        "rationale": "A same-scope 7% year-over-year denominator maps exclusively to anchor 2.",
        "missing_material_questions": [],
    }
    return {"dimensions": dimensions}


def _demand_lower_two_payload():
    dimensions = [_unknown_payload(name) for name in SEGMENT_DIMENSIONS]
    dimensions[0] = {
        "dimension": "demand_pressure",
        "rating_min": 2,
        "rating_max": 4,
        "evidence_state": "supported",
        "bound_type": "lower_bound",
        "bound_basis": {
            "floor_anchor": 2,
            "ceiling_anchor": None,
            "exact_basis": None,
            "unresolved_higher_anchors": [3, 4],
            "excluded_higher_anchors": [],
        },
        "evidence_ids": ["evidence-demand-1"],
        "stale": False,
        "rationale": "The evidence supports anchor 2 but does not resolve higher anchors.",
        "missing_material_questions": ["Is same-scope growth at least 10%?"],
    }
    return {"dimensions": dimensions}


def _demand_upper_two_payload():
    dimensions = [_unknown_payload(name) for name in SEGMENT_DIMENSIONS]
    dimensions[0] = {
        "dimension": "demand_pressure",
        "rating_min": 0,
        "rating_max": 2,
        "evidence_state": "supported",
        "bound_type": "upper_bound",
        "bound_basis": {
            "floor_anchor": None,
            "ceiling_anchor": 2,
            "exact_basis": None,
            "unresolved_higher_anchors": [],
            "excluded_higher_anchors": [3, 4],
        },
        "evidence_ids": ["evidence-demand-1"],
        "stale": False,
        "rationale": "A direct same-scope upper bound excludes growth anchors 3 and 4.",
        "missing_material_questions": ["What positive floor is directly supported?"],
    }
    return {"dimensions": dimensions}


def _demand_interval_one_three_payload():
    dimensions = [_unknown_payload(name) for name in SEGMENT_DIMENSIONS]
    dimensions[0] = {
        "dimension": "demand_pressure",
        "rating_min": 1,
        "rating_max": 3,
        "evidence_state": "supported",
        "bound_type": "interval",
        "bound_basis": {
            "floor_anchor": 1,
            "ceiling_anchor": 3,
            "exact_basis": None,
            "unresolved_higher_anchors": [2, 3],
            "excluded_higher_anchors": [4],
        },
        "evidence_ids": ["evidence-demand-1"],
        "stale": False,
        "rationale": "Direct evidence supports a floor of 1 and a ceiling of 3.",
        "missing_material_questions": ["Which anchor between 1 and 3 applies?"],
    }
    return {"dimensions": dimensions}


def test_segment_scorer_keeps_empty_evidence_unknown_without_calling_llm():
    """SELECT INVARIANT: missing evidence is unknown [0,4], never a model-imputed score."""
    client = NoCallClient()
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    draft = scorer.assess(
        _node(),
        claims=(),
        evidence_cards=(),
        request=_request(),
        contract_version="theme-chokepoint-scoring-v1.4",
    )

    assert tuple(item.dimension for item in draft.dimensions) == SEGMENT_DIMENSIONS
    assert all(
        item.evidence_state == "unknown"
        and item.bound_type == "none"
        and (item.rating_min, item.rating_max) == (0, 4)
        and item.evidence_ids == ()
        and item.rationale
        and item.missing_material_questions
        for item in draft.dimensions
    )
    assert draft.missing_material_fields == SEGMENT_DIMENSIONS
    assert draft.demand_direct_evidence is False
    assert draft.supply_direct_evidence is False
    assert draft.counter_evidence_search_complete is False
    assert draft.mandatory_conflict is False
    assert draft.relief_horizon is None
    assert client.chat.completions.calls == []


def test_segment_scorer_maps_valid_ledger_evidence_and_derives_flags_mechanically():
    """SELECT INVARIANT: the model proposes bounds; code derives gates and search state."""
    claim, card = _claim_and_card()
    client = FakeClient(_demand_exact_two_payload())
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    draft = scorer.assess(
        _node(),
        claims=(claim,),
        evidence_cards=(card,),
        request=_request(),
        contract_version="theme-chokepoint-scoring-v1.4",
    )

    demand = draft.dimensions[0]
    assert (demand.rating_min, demand.rating_max) == (2, 2)
    assert demand.bound_type == "exact"
    assert demand.bound_basis.excluded_higher_anchors == (3, 4)
    assert demand.evidence_ids == (card.evidence_id,)
    assert [item.condition_type for item in demand.anchor_conditions] == [
        "floor",
        "ceiling",
    ]
    assert demand.anchor_conditions[0].decisive_claim_ids == (claim.claim_id,)
    assert demand.decisive_evidence_ids == (card.evidence_id,)
    assert draft.missing_material_fields == SEGMENT_DIMENSIONS[1:]
    assert draft.demand_direct_evidence is True
    assert draft.supply_direct_evidence is False
    assert draft.counter_evidence_search_complete is False
    assert draft.mandatory_conflict is False
    assert draft.relief_horizon is None
    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["temperature"] == 0
    assert "same-scope comparable denominator" in call["messages"][0]["content"]
    assert '"dimensions": [' in call["messages"][0]["content"]
    assert '"bound_basis": {' in call["messages"][0]["content"]
    assert '"missing_material_questions": []' in call["messages"][0]["content"]
    assert '"evidence_state": "unknown"' in call["messages"][0]["content"]
    assert '"source_ambiguity": false' in call["messages"][1]["content"]
    assert card.exact_quote in call["messages"][1]["content"]
    assert "AS_OF_DATE: 2026-08-16" in call["messages"][1]["content"]
    assert "GEOGRAPHY: US" in call["messages"][1]["content"]


def test_segment_scorer_rejects_exact_below_four_without_upper_bound_capability():
    """SELECT INVARIANT: an exact ceiling cannot be created from general evidence."""
    claim, card = _claim_and_card(claim_capabilities=("general_scoring_evidence",))
    scorer = DeepSeekV14SegmentScorer(
        client=FakeClient(_demand_exact_two_payload()), model="test-model"
    )

    with pytest.raises(RuntimeError, match="after one repair"):
        scorer.assess(
            _node(),
            claims=(claim,),
            evidence_cards=(card,),
            request=_request(),
            contract_version="theme-chokepoint-scoring-v1.4",
        )


def test_segment_scorer_allows_floor_only_lower_bound_but_not_primary_gate_credit():
    """SELECT INVARIANT: floor_only reuse may prove <=2, never direct Primary evidence."""
    claim, card = _claim_and_card(scoring_use="floor_only")
    client = FakeClient(_demand_lower_two_payload())
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    draft = scorer.assess(
        _node(),
        claims=(claim,),
        evidence_cards=(card,),
        request=_request(),
        contract_version="theme-chokepoint-scoring-v1.4",
    )

    demand = draft.dimensions[0]
    assert (demand.rating_min, demand.rating_max) == (2, 4)
    assert demand.bound_type == "lower_bound"
    assert demand.bound_basis.floor_anchor == 2
    assert demand.bound_basis.unresolved_higher_anchors == (3, 4)
    assert "demand_pressure" in draft.missing_material_fields
    assert draft.demand_direct_evidence is False


def test_segment_scorer_accepts_upper_bound_only_with_complete_ceiling_basis():
    """SELECT INVARIANT: a supported ceiling, not search absence, creates [0,k]."""
    claim, card = _claim_and_card()
    client = FakeClient(_demand_upper_two_payload())
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    draft = scorer.assess(
        _node(),
        claims=(claim,),
        evidence_cards=(card,),
        request=_request(),
        contract_version="theme-chokepoint-scoring-v1.4",
    )

    demand = draft.dimensions[0]
    assert (demand.rating_min, demand.rating_max) == (0, 2)
    assert demand.bound_type == "upper_bound"
    assert demand.bound_basis.floor_anchor is None
    assert demand.bound_basis.ceiling_anchor == 2
    assert demand.bound_basis.excluded_higher_anchors == (3, 4)
    assert "demand_pressure" in draft.missing_material_fields


def test_segment_scorer_accepts_finite_interval_with_floor_and_ceiling_basis():
    """SELECT INVARIANT: a finite interval preserves both supported boundaries."""
    claim, card = _claim_and_card()
    client = FakeClient(_demand_interval_one_three_payload())
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    draft = scorer.assess(
        _node(),
        claims=(claim,),
        evidence_cards=(card,),
        request=_request(),
        contract_version="theme-chokepoint-scoring-v1.4",
    )

    demand = draft.dimensions[0]
    assert (demand.rating_min, demand.rating_max) == (1, 3)
    assert demand.bound_type == "interval"
    assert demand.bound_basis.floor_anchor == 1
    assert demand.bound_basis.ceiling_anchor == 3
    assert demand.bound_basis.unresolved_higher_anchors == (2, 3)
    assert demand.bound_basis.excluded_higher_anchors == (4,)


def test_segment_scorer_rejects_conflicted_without_support_and_contradiction():
    """SELECT INVARIANT: conflicted requires both evidence stances in one dimension."""
    claim, card = _claim_and_card(stance="supports")
    payload = _demand_interval_one_three_payload()
    payload["dimensions"][0]["evidence_state"] = "conflicted"
    client = FakeClient(payload)
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    with pytest.raises(RuntimeError, match="after one repair"):
        scorer.assess(
            _node(),
            claims=(claim,),
            evidence_cards=(card,),
            request=_request(),
            contract_version="theme-chokepoint-scoring-v1.4",
        )


@pytest.mark.parametrize(
    "payload_factory",
    [
        _demand_exact_two_payload,
        _demand_lower_two_payload,
        _demand_upper_two_payload,
        _demand_interval_one_three_payload,
    ],
)
def test_segment_scorer_rejects_supported_bounds_backed_only_by_contradiction(
    payload_factory,
):
    """SELECT INVARIANT: contradicting evidence cannot authorize a supported bound."""
    claim, card = _claim_and_card(stance="contradicts")
    scorer = DeepSeekV14SegmentScorer(
        client=FakeClient(payload_factory()), model="test-model"
    )

    with pytest.raises(RuntimeError, match="after one repair"):
        scorer.assess(
            _node(),
            claims=(claim,),
            evidence_cards=(card,),
            request=_request(),
            contract_version="theme-chokepoint-scoring-v1.4",
        )


def test_segment_scorer_rejects_mixed_support_and_contradiction_as_supported():
    """SELECT INVARIANT: mixed decisive stances are conflicted, never supported."""
    support_claim, support_card = _claim_and_card(stance="supports")
    contradict_claim = replace(
        support_claim,
        claim_id="claim-demand-2",
        evidence_ids=("evidence-demand-2",),
    )
    contradict_card = replace(
        support_card,
        evidence_id="evidence-demand-2",
        claim_id=contradict_claim.claim_id,
        article_id="article-demand-2",
        canonical_url="https://issuer.example.com/demand-contradiction",
        stance="contradicts",
        origin_event_id="event-demand-2",
        evidence_family_id="family-demand-2",
    )
    payload = _demand_lower_two_payload()
    payload["dimensions"][0]["evidence_ids"] = [
        support_card.evidence_id,
        contradict_card.evidence_id,
    ]
    scorer = DeepSeekV14SegmentScorer(client=FakeClient(payload), model="test-model")

    with pytest.raises(RuntimeError, match="after one repair"):
        scorer.assess(
            _node(),
            claims=(support_claim, contradict_claim),
            evidence_cards=(support_card, contradict_card),
            request=_request(),
            contract_version="theme-chokepoint-scoring-v1.4",
        )


def test_segment_scorer_repairs_one_invalid_model_contract_response():
    """SELECT INVARIANT: one invalid model response gets one bounded repair attempt."""
    claim, card = _claim_and_card()
    client = SequencedClient([{"dimensions": []}, _demand_exact_two_payload()])
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    draft = scorer.assess(
        _node(),
        claims=(claim,),
        evidence_cards=(card,),
        request=_request(),
        contract_version="theme-chokepoint-scoring-v1.4",
    )

    assert draft.dimensions[0].rating_min == 2
    assert len(client.chat.completions.calls) == 2
    repair_messages = client.chat.completions.calls[1]["messages"]
    assert "repair" in repair_messages[-1]["content"].casefold()
    assert "dimensions\": []" not in repair_messages[-1]["content"]


def test_segment_scorer_repair_explains_exact_basis_null_and_enum_contract():
    """SELECT INVARIANT: exact_basis literal errors receive a deterministic repair rule."""
    claim, card = _claim_and_card()
    invalid = _demand_lower_two_payload()
    invalid["dimensions"][0]["bound_basis"]["exact_basis"] = "none"
    client = SequencedClient([invalid, _demand_lower_two_payload()])
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    scorer.assess(
        _node(),
        claims=(claim,),
        evidence_cards=(card,),
        request=_request(),
        contract_version="theme-chokepoint-scoring-v1.4",
    )

    repair = client.chat.completions.calls[1]["messages"][-1]["content"]
    assert "exact_basis must be JSON null unless bound_type is exact" in repair
    assert "natural_cap, direct_upper_bound, or contract_exclusivity" in repair


def test_segment_scorer_rejects_model_generated_total_score():
    """SELECT INVARIANT: the model may return ordinals only, never derived conclusions."""
    claim, card = _claim_and_card()
    payload = _demand_exact_two_payload()
    payload["total_score"] = 88
    client = FakeClient(payload)
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    with pytest.raises(RuntimeError, match="after one repair"):
        scorer.assess(
            _node(),
            claims=(claim,),
            evidence_cards=(card,),
            request=_request(),
            contract_version="theme-chokepoint-scoring-v1.4",
        )

    assert len(client.chat.completions.calls) == 2


def test_segment_scorer_fails_closed_without_echoing_invalid_payload():
    """REGRESSION: the bounded repair failure never exposes raw model content."""
    claim, card = _claim_and_card()
    secret_payload = "not-json TOP_SECRET_MODEL_PAYLOAD"
    client = SequencedClient([secret_payload, secret_payload])
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    with pytest.raises(RuntimeError) as captured:
        scorer.assess(
            _node(),
            claims=(claim,),
            evidence_cards=(card,),
            request=_request(),
            contract_version="theme-chokepoint-scoring-v1.4",
        )

    assert "TOP_SECRET_MODEL_PAYLOAD" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert len(client.chat.completions.calls) == 2


def test_segment_scorer_reports_safe_semantic_violation_without_raw_payload():
    """SELECT INVARIANT: failure diagnostics name the rule, never the model payload."""
    claim, card = _claim_and_card()
    payload = _demand_exact_two_payload()
    payload["dimensions"][0]["evidence_ids"] = ["SECRET_NOT_IN_LEDGER"]
    client = FakeClient(payload)
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    with pytest.raises(RuntimeError) as captured:
        scorer.assess(
            _node(),
            claims=(claim,),
            evidence_cards=(card,),
            request=_request(),
            contract_version="theme-chokepoint-scoring-v1.4",
        )

    message = str(captured.value)
    assert "resolved dimensions must cite supplied Evidence Cards" in message
    assert "SECRET_NOT_IN_LEDGER" not in message


def test_segment_scorer_reports_safe_schema_path_without_input_value():
    """SELECT INVARIANT: schema diagnostics expose only field paths and error types."""
    claim, card = _claim_and_card()
    payload = _demand_exact_two_payload()
    del payload["dimensions"][0]["missing_material_questions"]
    payload["dimensions"][0]["rationale"] = "SECRET_RATIONALE_VALUE"
    client = FakeClient(payload)
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    with pytest.raises(RuntimeError) as captured:
        scorer.assess(
            _node(),
            claims=(claim,),
            evidence_cards=(card,),
            request=_request(),
            contract_version="theme-chokepoint-scoring-v1.4",
        )

    message = str(captured.value)
    assert "schema_violation:dimensions.0.missing_material_questions:missing" in message
    assert "SECRET_RATIONALE_VALUE" not in message


@pytest.mark.parametrize(
    "case",
    ["unknown_evidence_id", "cross_dimension", "context_only", "floor_only_high"],
)
def test_segment_scorer_rejects_evidence_without_permitted_scoring_ownership(case):
    """REGRESSION: model bounds cannot exceed persisted evidence ownership."""
    if case == "cross_dimension":
        claim, card = _claim_and_card(dimension="capacity_inelasticity")
        payload = _demand_exact_two_payload()
    elif case == "context_only":
        claim, card = _claim_and_card(scoring_use="context_only")
        payload = _demand_exact_two_payload()
    elif case == "floor_only_high":
        claim, card = _claim_and_card(scoring_use="floor_only")
        payload = _demand_lower_two_payload()
        payload["dimensions"][0].update(rating_min=3)
        payload["dimensions"][0]["bound_basis"].update(
            floor_anchor=3,
            unresolved_higher_anchors=[4],
        )
    else:
        claim, card = _claim_and_card()
        payload = _demand_exact_two_payload()
        payload["dimensions"][0]["evidence_ids"] = ["not-in-ledger"]
    client = FakeClient(payload)
    scorer = DeepSeekV14SegmentScorer(client=client, model="test-model")

    with pytest.raises(RuntimeError, match="after one repair"):
        scorer.assess(
            _node(),
            claims=(claim,),
            evidence_cards=(card,),
            request=_request(),
            contract_version="theme-chokepoint-scoring-v1.4",
        )

    assert len(client.chat.completions.calls) == 2
