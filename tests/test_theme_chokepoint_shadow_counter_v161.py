from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import pytest

from event_collector.theme_chokepoint.contracts import CounterSearchRawProviderResponse
from tests.test_theme_chokepoint_stage3_shadow_v161 import setup_shadow


class CounterTransport:
    identity = 'controlled-three-route-counter-v1'

    def __init__(self, found=None, unknown=None):
        self.calls = []
        self.found = found
        self.unknown = unknown

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        route = kwargs['route_id']
        dimension = {'demand': 'demand_pressure', 'supply': 'effective_supply_concentration',
                     'alternatives': 'substitute_weakness'}[route]
        evidence = next(card.evidence_id for card in kwargs['evidence_cards'] if card.primary_scoring_dimension == dimension)
        found = route == self.found
        payload = {
            'query_log_id': 'query-' + route, 'status': 'supported' if found else 'explicit_negative',
            'evidence_ids': [evidence], 'coverage_state': 'found' if found else 'explicit_negative',
            'counter_evidence': ([{'canonical_url': 'https://counter.example/' + route,
                                  'original_text': 'Orders were cancelled in this scoped market.',
                                  'exact_quote': 'Orders were cancelled in this scoped market.',
                                  'claim_statement': 'Orders were cancelled in this scoped market.'}] if found else []),
            'finding': 'Executed scoped source search for ' + route,
        }
        if route == self.unknown:
            payload.update(status='unknown', coverage_state='unknown', evidence_ids=[])
        return CounterSearchRawProviderResponse(
            provider='controlled-counter', provider_trace_id='trace-' + route,
            http_status=200, raw_body=json.dumps(payload).encode(),
            retrieved_at=datetime.now(timezone.utc), cost_usd=0.01,
        )


def positive_extractor(extractor, completions):
    def create(**kwargs):
        completions.calls.append(kwargs)
        content = kwargs['messages'][1]['content']
        evidence = json.loads(content.split('CASE_BOUND_EVIDENCE_JSON:\n')[1].split('\n\nOUTPUT_SCHEMA_JSON:')[0])
        ids = {row['primary_scoring_dimension']: [row['evidence_id']] for row in evidence}
        facts = {
            'schema_version': 'theme-chokepoint-segment-facts-v1.6.1',
            'demand_pressure': dict(unresolved=False, canonical_annual_growth=.25, direct_transmission_verified=True,
                inventory_verified=False, annual_growth_basis='monthly_yoy', demand_proxy_annual_growth=None,
                demand_proxy_directly_attributed=False, demand_proxy_capacity_prebooked=False),
            'downstream_criticality': dict(unresolved=False, scope_dependency_verified=True,
                structural_hard_block='unknown', explicit_no_impact=False, cohorts=[]),
            'effective_supply_concentration': dict(unresolved=False, target_demand=None, suppliers=[],
                sole_effective_supplier_verified=True, qualified_failover_absence_verified=True,
                effective_supplier_count_upper_bound=1),
            'qualification_barrier': dict(unresolved=False, duration_months=18, qualification_required=True,
                explicit_no_special_qualification=False, process_signals=['type_test'], scope_coverage='full_scope'),
            'capacity_inelasticity': dict(unresolved=False, top1_loss_output=None, failover_output_90d=None,
                stable_incremental_output=None, tasks=[], minimum_physical_lead_time_months=12,
                physical_expansion_required=True, physical_constraint_signals=['equipment_build']),
            'substitute_weakness': dict(unresolved=False, target_demand=None, discovery_rounds_without_new_routes=0,
                required_categories=['different_technology'], searched_categories=[], explicit_negative_categories=[],
                budget_exhausted=False, routes=[dict(route_id='alternative', status='credible', qualified_output=None,
                capacity_pool_id=None, category='different_technology')]),
        }
        for dimension, evidence_ids in ids.items():
            facts[dimension]['evidence_ids'] = evidence_ids
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(facts)))])
    completions.create = create
    return extractor


def test_shadow_executes_three_counter_routes_and_derives_final_state(tmp_path):
    """SELECT INVARIANT: fresh reconciled counter-search actually enables state evaluation."""
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    positive_extractor(extractor, completions)
    counter = CounterTransport()
    before = repo.get_run(request.run_id)
    result = loop.run_v161_shadow(request.run_id, extractor=extractor, artifact_root=tmp_path/'shadow',
                                  shadow_id='full', counter_executor=counter)
    assert [row['route_id'] for row in counter.calls] == ['demand', 'supply', 'alternatives']
    segment = result['segments'][0]
    assert segment['state_status'] == 'evaluated'
    assert segment['primary_state'] == 'candidate_chokepoint'
    assert segment['counter_context']['counter_evidence_search_complete'] is True
    assert len(segment['counter_receipt']['route_findings']) == 3
    assert repo.list_counter_search_requests(request.run_id) == ()
    assert repo.get_stage3_result(request.run_id) == baseline
    assert repo.get_run(request.run_id) == before


@pytest.mark.parametrize('found,unknown', [('demand', None), ('supply', None), ('alternatives', None), (None, 'demand')])
def test_counter_findings_and_unknown_routes_prevent_candidate(tmp_path, found, unknown):
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    positive_extractor(extractor, completions)
    counter = CounterTransport(found=found, unknown=unknown)
    result = loop.run_v161_shadow(request.run_id, extractor=extractor, artifact_root=tmp_path/'shadow',
                                  shadow_id='counterexample', counter_executor=counter)
    segment = result['segments'][0]
    assert segment['primary_state'] not in ('candidate_chokepoint', 'strong_candidate_chokepoint')
    assert segment['counter_context']['mandatory_conflict'] is bool(found)
    assert segment['counter_context']['counter_evidence_search_complete'] is (unknown is None)
    if found:
        assert len(segment['counter_evidence']) == 1
        evidence = segment['counter_evidence'][0]
        assert evidence['route_id'] == found
        assert evidence['candidate']['original_text'] == 'Orders were cancelled in this scoped market.'
        assert evidence['evidence_id'] not in {card.evidence_id for card in baseline.evidence_cards}
    assert repo.get_stage3_result(request.run_id) == baseline


def test_full_shadow_requires_counter_executor_before_inference(tmp_path):
    """SELECT INVARIANT: full mode cannot silently turn into facts-only evaluation."""
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    with pytest.raises(ValueError, match='counter executor'):
        loop.run_v161_shadow(request.run_id, extractor=extractor, artifact_root=tmp_path/'shadow', shadow_id='full')
    assert not completions.calls


def test_shadow_reconciles_original_source_independence_read_only(tmp_path, monkeypatch):
    """SELECT INVARIANT: source independence comes from the original persisted source store."""
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    positive_extractor(extractor, completions)
    checked = []
    def reconcile(card):
        checked.append(card.evidence_id)
        return card.evidence_id
    monkeypatch.setattr(repo, 'reconcile_independent_source_event', reconcile)
    result = loop.run_v161_shadow(request.run_id, extractor=extractor, artifact_root=tmp_path/'shadow',
                                  shadow_id='sources', counter_executor=CounterTransport())
    assert len(checked) == 1
    assert result['segments'][0]['counter_context']['independent_supply_constraint_count'] == 1


def test_completed_counter_receipt_is_reused_and_transport_change_rejected(tmp_path):
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    counter = CounterTransport()
    kwargs = dict(extractor=extractor, artifact_root=tmp_path/'shadow', shadow_id='cached', counter_executor=counter)
    first = loop.run_v161_shadow(request.run_id, **kwargs)
    assert loop.run_v161_shadow(request.run_id, **kwargs) == first
    assert len(completions.calls) == 1 and len(counter.calls) == 3
    counter.identity = 'changed-transport'
    with pytest.raises(ValueError, match='identity changed'):
        loop.run_v161_shadow(request.run_id, **kwargs)
    assert len(counter.calls) == 3


def test_counter_executor_requires_stable_identity_before_spend(tmp_path):
    """SELECT INVARIANT: anonymous transports cannot share a cached assessment identity."""
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    counter = CounterTransport()
    counter.identity = None
    with pytest.raises(ValueError, match='counter executor identity'):
        loop.run_v161_shadow(request.run_id, extractor=extractor, artifact_root=tmp_path/'shadow',
                              shadow_id='anonymous', counter_executor=counter)
    assert not completions.calls and not counter.calls


def test_cached_counter_assessment_rejects_changed_query_scope(tmp_path, monkeypatch):
    """SELECT INVARIANT: changing the graph label changes the search input identity."""
    repo, request, loop, baseline, extractor, completions = setup_shadow(tmp_path)
    counter = CounterTransport()
    kwargs = dict(extractor=extractor, artifact_root=tmp_path/'shadow', shadow_id='graph', counter_executor=counter)
    loop.run_v161_shadow(request.run_id, **kwargs)
    graph = repo.get_supply_chain_graph(request.run_id)
    altered = replace(graph, nodes=tuple(replace(node, normalized_name='different scope') for node in graph.nodes))
    monkeypatch.setattr(repo, 'get_supply_chain_graph', lambda run_id: altered)
    with pytest.raises(ValueError, match='identity changed'):
        loop.run_v161_shadow(request.run_id, **kwargs)
    assert len(counter.calls) == 3
