from dataclasses import replace
import pytest

from event_collector.theme_chokepoint.runtime_v161 import V161SegmentScorer, RUNTIME_VERSION
from event_collector.theme_chokepoint.stage3 import _validate_segment_draft_evidence, _validate_dimension
from tests.test_theme_chokepoint_stage3_shadow_v161 import setup_shadow
from tests.test_theme_chokepoint_shadow_counter_v161 import positive_extractor


def test_runtime_fact_scorer_returns_ledger_bound_code_computed_dimensions(tmp_path):
    """SELECT INVARIANT: the runtime scorer uses v161 facts, never model ratings."""
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    positive_extractor(extractor, completions)
    original = extractor.extract
    extractor.extract = lambda **kwargs: replace(original(**kwargs), ratings={})
    scorer = V161SegmentScorer(extractor)
    node = next(item for item in repo.get_supply_chain_graph(request.run_id).nodes if item.depth > 0)
    draft = scorer.assess(node, baseline.claims, baseline.evidence_cards,
                         request=request, contract_version=RUNTIME_VERSION)
    assert {item.dimension: (item.rating_min, item.rating_max) for item in draft.dimensions} == {
        'demand_pressure': (3, 4), 'downstream_criticality': (4, 4),
        'effective_supply_concentration': (4, 4), 'qualification_barrier': (4, 4),
        'capacity_inelasticity': (3, 4), 'substitute_weakness': (0, 2)}
    _validate_segment_draft_evidence(draft, node=node, claims=baseline.claims,
        evidence_cards=baseline.evidence_cards, request=request)
    for dimension in draft.dimensions:
        _validate_dimension(dimension, {card.evidence_id for card in baseline.evidence_cards})


def test_empty_runtime_evidence_requests_all_dimensions_without_model_io(tmp_path):
    """SELECT INVARIANT: the first pass gathers evidence before fact extraction."""
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    node = next(item for item in repo.get_supply_chain_graph(request.run_id).nodes if item.depth > 0)
    draft = V161SegmentScorer(extractor).assess(node, (), (), request=request, contract_version=RUNTIME_VERSION)
    assert len(draft.missing_material_fields) == 6
    assert all(item.evidence_state == 'unknown' and item.evidence_ids == () for item in draft.dimensions)
    assert completions.calls == []


@pytest.mark.parametrize('unknown', [False, True])
def test_v161_main_loop_persists_frozen_state_and_distinct_contract(tmp_path, unknown):
    """SELECT INVARIANT: v161 runtime state reaches canonical Stage 3 with honest lineage."""
    from event_collector.theme_chokepoint.stage3 import EvidenceChokepointLoop
    from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
    from event_collector.theme_chokepoint.providers.critic import EvidenceBoundSegmentCritic
    from event_collector.theme_chokepoint.providers.counter_search import EvidenceBoundCounterSearchProvider
    from tests.test_theme_chokepoint_stage3 import (
        _seed_run, RecordingAcquirer, _all_dimension_candidates, CONTROLLED_GOVERNANCE_BUNDLE_SHA256,
    )
    from tests.test_theme_chokepoint_shadow_counter_v161 import CounterTransport
    _, _, _, _, extractor, completions = setup_shadow(tmp_path/'fixture')
    positive_extractor(extractor, completions)
    repo = ThemeChokepointRepository(tmp_path/'runtime.db')
    request = _seed_run(repo)
    counter = CounterTransport(unknown='demand' if unknown else None)
    critic = EvidenceBoundSegmentCritic(EvidenceBoundCounterSearchProvider(counter, repository=repo,
        max_queries=3, max_time_seconds=90, max_cost_usd=5))
    loop = EvidenceChokepointLoop(repo, RecordingAcquirer(_all_dimension_candidates()),
        V161SegmentScorer(extractor), critic=critic, enable_v161_segment_chain=True,
        allow_unfrozen_overlay=True, expected_governance_bundle_sha256=CONTROLLED_GOVERNANCE_BUNDLE_SHA256)
    result = loop.run(request.run_id)
    assert result.assessments[0].score_min == 72.5
    assert result.assessments[0].primary_state == (None if unknown else 'candidate_chokepoint')
    if unknown:
        assert result.status.value == 'INCOMPLETE_BUDGET_EXHAUSTED'
        assert 'v161_state_eligibility_unproven' in result.incomplete_reasons
    assert result.contract_version == RUNTIME_VERSION
    assert result.executable_contract_id == RUNTIME_VERSION
    assert repo.get_stage3_result(request.run_id) == result
    assert len(counter.calls) == 3


def test_production_composition_installs_same_v161_contract_in_both_scoring_stages(tmp_path):
    """SELECT INVARIANT: public composition carries the explicit staging mode end to end."""
    from types import SimpleNamespace
    from event_collector.theme_chokepoint.production import build_production_theme_chokepoint_runtime
    from tests.test_theme_chokepoint_production_composition import _config, _session_factory
    config = replace(_config(tmp_path), enable_v161_segment_chain=True)
    runtime = build_production_theme_chokepoint_runtime(config, theme_framer=SimpleNamespace(),
        product_anchor_proposer=SimpleNamespace(), dependency_proposer=SimpleNamespace(),
        evidence_acquirer=SimpleNamespace(), segment_scorer=V161SegmentScorer(SimpleNamespace()),
        counter_search_executor=SimpleNamespace(), session_factory=_session_factory)
    assert runtime.stage3.contract.version == runtime.stage4.contract.version == RUNTIME_VERSION
    assert runtime.stage3.contract.executable_contract_sha256 == runtime.stage4.contract.executable_contract_sha256
    assert runtime.orchestrator.contract_id == RUNTIME_VERSION
    assert runtime.stage3.contract.governance_status == 'staging_not_production'


def test_runtime_rejects_cross_scope_evidence_before_model_io(tmp_path):
    """SELECT INVARIANT: scoped runtime requests cannot spend on another assessment's evidence."""
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    positive_extractor(extractor, completions)
    node = next(item for item in repo.get_supply_chain_graph(request.run_id).nodes if item.depth > 0)
    with pytest.raises(ValueError, match='Scope'):
        V161SegmentScorer(extractor).assess(node, baseline.claims, baseline.evidence_cards,
            request=replace(request, region='different region'), contract_version=RUNTIME_VERSION)
    assert completions.calls == []


def test_v161_empty_final_assessment_preserves_unknown(tmp_path):
    from event_collector.theme_chokepoint.runtime_v161 import finalize_assessment
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    node = next(item for item in repo.get_supply_chain_graph(request.run_id).nodes if item.depth > 0)
    draft = V161SegmentScorer(extractor).assess(node, (), (), request=request, contract_version=RUNTIME_VERSION)
    result = finalize_assessment(draft, loop.contract.weights, set(), RUNTIME_VERSION)
    assert result.primary_state is None
    assert (result.score_min, result.score_max) == (0, 100)
    assert result.unknown_weight_share == 1


def test_context_only_sources_cannot_trigger_runtime_fact_scoring(tmp_path):
    """SELECT INVARIANT: unverified source dates leave scoring unknown, not a failed model cycle."""
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    node = next(item for item in repo.get_supply_chain_graph(request.run_id).nodes if item.depth > 0)
    cards = tuple(replace(card, scoring_use='context_only', scoring_eligible=False) for card in baseline.evidence_cards)
    claims = tuple(replace(claim, scoring_use='context_only', scoring_eligible=False) for claim in baseline.claims)
    draft = V161SegmentScorer(extractor).assess(node, claims, cards, request=request, contract_version=RUNTIME_VERSION)
    assert completions.calls == []
    assert all(item.evidence_state == 'unknown' for item in draft.dimensions)
