"""Fresh counter-search provenance and frozen state math for isolated shadow runs."""

from dataclasses import asdict, replace
from hashlib import sha256
import json
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path

from event_collector.theme_chokepoint.contracts import BoundBasis, DimensionRatingDraft, SegmentScoreDraft
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.providers.counter_search import EvidenceBoundCounterSearchProvider
from event_collector.theme_chokepoint.providers.critic import EvidenceBoundSegmentCritic


class _CounterStore(ThemeChokepointRepository):
    """Write search lineage locally; resolve original source identity read-only."""

    def __init__(self, path, original):
        super().__init__(path)
        self.original = original

    def reconcile_independent_source_event(self, card):
        return self.original.reconcile_independent_source_event(card)


def evaluate(*, repository, run_id, shadow_id, output, root, contract, case, facts, ratings,
             baseline, executor):
    case_id = case['case_id']
    scope = case['assessment_scope']
    scoped_id = sha256(json.dumps([run_id, shadow_id, case_id]).encode()).hexdigest()
    cards = tuple(card for card in baseline.evidence_cards if card.evidence_id in case['evidence_ids'])
    claims = tuple(claim for claim in baseline.claims if claim.claim_id in {card.claim_id for card in cards})
    request = replace(repository.get_run(run_id).request, run_id=scoped_id,
                      region=scope['geography'], as_of_date=cards[0].assessment_scope.as_of_date,
                      time_horizon_months=scope['time_horizon_months'])
    node = next(node for node in repository.get_supply_chain_graph(run_id).nodes
                if node.node_id == scope['segment_id'])
    node = replace(node, normalized_name=f"{node.normalized_name} {scope['product_id']} "
                   f"{scope['customer_or_platform_scope']} {scope['time_horizon_months']} months")
    ordinals = []
    dimensions = []
    for name, (low, high) in ratings.items():
        unknown = (low, high) == (0, 4)
        kind = ('none' if unknown else 'exact' if low == high else 'lower_bound' if high == 4
                else 'upper_bound' if low == 0 else 'interval')
        basis = BoundBasis(
            floor_anchor=low if kind in ('exact', 'lower_bound', 'interval') else None,
            ceiling_anchor=high if kind in ('exact', 'upper_bound', 'interval') else None,
            exact_basis=('natural_cap' if high == 4 else 'contract_exclusivity') if kind == 'exact' else None,
            unresolved_higher_anchors=tuple(range(low + 1, 5)) if kind == 'lower_bound' else (),
            excluded_higher_anchors=tuple(range(high + 1, 5)) if kind in ('exact', 'upper_bound', 'interval') else (),
        )
        state = 'unknown' if unknown else 'supported'
        ids = tuple(facts[name]['evidence_ids'])
        dimensions.append(DimensionRatingDraft(name, low, high, state, kind, basis, ids, False))
        ordinals.append(dict(item_id=name, evidence_state=state, bound_type=kind,
                             rating_min=low, rating_max=high, bound_basis=asdict(basis),
                             primary_evidence_ids=ids, bound_derivation='unresolved' if unknown else 'rule_derived',
                             rationale='Bounds computed from validated v1.6.1 facts and source evidence.'))
    draft = SegmentScoreDraft(node.node_id, tuple(dimensions), (), False, False, False, False, None)
    output.mkdir(parents=True, exist_ok=True)
    sidecar_path = output / (scoped_id + '.counter.sqlite3')
    sidecar = _CounterStore(sidecar_path, repository)
    sidecar.create_request(request)
    provider = EvidenceBoundCounterSearchProvider(
        executor, repository=sidecar, max_queries=3, max_time_seconds=request.max_time_seconds,
        max_cost_usd=request.max_cost_usd,
    )
    reviewed = EvidenceBoundSegmentCritic(provider).apply(
        node=node, ordinal_draft=draft, claims=claims, evidence_cards=cards, request=request,
    )
    context = {key: getattr(reviewed, key) for key in (
        'demand_direct_evidence', 'supply_direct_evidence', 'counter_evidence_search_complete',
        'mandatory_conflict', 'independent_supply_constraint_count', 'key_source_quality_high',
    )}
    receipt = reviewed.critic_receipt
    scope_hash = sha256('\0'.join((scoped_id, node.node_id, request.as_of_date.isoformat(), request.region)).encode()).hexdigest()
    counter_evidence = []
    for reconciliation in sidecar.list_reconciled_counter_routes(scoped_id, node.node_id, scope_hash):
        for item in sidecar.load_reconciled_counter_evidence(reconciliation.receipt_id):
            counter_evidence.append({**vars(item), 'candidate': asdict(item.candidate)})
    metadata = dict(task_id=scoped_id, contract_id='theme-chokepoint-scoring-v1.6+human-inference-overlay-v1.6.1',
                    evidence_pack_id=case_id)
    gates = ('segment_discovery', 'counter_search_complete', 'strong_candidate_evidence')
    task = dict(**metadata, allowed_annotator_roles=['shadow_fact_adapter'], segment_weights=contract.segment_weights,
                ordinal_tasks=[dict(item_id=name, case_id=case_id, dimension=name) for name in ratings],
                hard_gate_tasks=[dict(item_id=gate, case_id=case_id, gate=gate) for gate in gates],
                state_tasks=[dict(item_id=case_id, case_id=case_id, ordinal_item_ids=list(ratings), hard_gate_item_ids=gates)])
    pack = {'cases': [{**case, 'state_context': context}]}
    result = dict(**metadata, annotator_id='v161-shadow-facts', annotator_role='shadow_fact_adapter',
                  independent_work_attestation=True, ordinal=ordinals)
    with TemporaryDirectory(prefix='state-', dir=output) as directory:
        paths = [Path(directory)/name for name in ('task.json', 'pack.json', 'ordinal.json', 'computed.json')]
        for path, data in zip(paths, (task, pack, result)):
            path.write_text(json.dumps(data), encoding='utf-8')
        subprocess.run(['node', str(root/'tools/theme-chokepoint/materialize-calibration.mjs'),
                        *map(str, paths), '--from-ordinals'], check=True, capture_output=True, text=True, timeout=30)
        computed = json.loads(paths[-1].read_text(encoding='utf-8'))
    return dict(primary_state=computed['state'][0]['primary_state'], state_status='evaluated',
                counter_context=context, counter_receipt=asdict(receipt), counter_evidence=counter_evidence,
                counter_store=str(sidecar_path.resolve()), state_calculation=computed)
