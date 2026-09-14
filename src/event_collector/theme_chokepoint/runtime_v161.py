"""Explicit staging composition of v161 Segment facts and governed v14 Company rules."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from event_collector.theme_chokepoint.contracts import (
    AnchorConditionResult, BoundBasis, DimensionRatingDraft, SegmentScoreDraft, SegmentAssessment,
)
from event_collector.theme_chokepoint.providers.facts_v161 import (
    _decode_and_score_v161, V161_FACT_PROMPT_SHA256, V161_FACT_SCHEMA_SHA256,
)
from event_collector.theme_chokepoint.validation_v161 import DIMENSIONS
from event_collector.theme_chokepoint.scoring_v16 import V16ScoringContract

RUNTIME_VERSION = 'theme-chokepoint-staging-segment-v1.6.1-company-v1.4'
ROOT = Path(__file__).resolve().parents[3]
CALIBRATION = ROOT/'outputs/theme-chokepoint-v1.6-r7-evaluation-20260823-001/post-adjudication-blind-package/calibration'


def configure_contract(contract):
    if contract.governance_status != 'controlled_unfrozen':
        raise ValueError('v161 staging requires explicit controlled overlay authorization')
    segment = V16ScoringContract(ROOT/'tools/theme-chokepoint/semantic-task-contract-v1.6.json',
        expected_sha256='c736ffd60eb0a905d93ff7707292e913dff4a5ca3a5c5a6699d53e9e4699e046')
    files = [Path(__file__), ROOT/'tools/theme-chokepoint/materialize-calibration.mjs',
             CALIBRATION/'semantic-validator-v1.6-eval.mjs', CALIBRATION/'semantic-validator-base-v1.5.mjs',
             ROOT/'src/event_collector/theme_chokepoint/scoring_v16.py',
             ROOT/'src/event_collector/theme_chokepoint/validation_v161.py']
    identity = dict(company_contract_sha256=contract.executable_contract_sha256,
        segment_contract_sha256=segment.sha256, prompt_sha256=V161_FACT_PROMPT_SHA256,
        schema_sha256=V161_FACT_SCHEMA_SHA256,
        code_sha256={str(file.relative_to(ROOT)): sha256(file.read_bytes()).hexdigest() for file in files})
    contract.version = contract.executable_contract_id = RUNTIME_VERSION
    contract.executable_contract_sha256 = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    contract.weights = segment.segment_weights
    contract.governance_status = 'staging_not_production'


def finalize_assessment(draft, weights, evidence_ids, contract_version):
    """Use the same frozen state evaluator as calibration, retaining critic receipts."""
    from event_collector.theme_chokepoint.stage3 import _validate_dimension
    if set(item.dimension for item in draft.dimensions) != set(weights) or len(draft.dimensions) != len(weights):
        raise ValueError('v161 runtime dimensions must match the contract')
    for item in draft.dimensions:
        _validate_dimension(item, evidence_ids)
    metadata = dict(task_id=draft.segment_id, contract_id=RUNTIME_VERSION, evidence_pack_id=draft.segment_id)
    gates = ('segment_discovery', 'counter_search_complete', 'strong_candidate_evidence')
    task = dict(**metadata, allowed_annotator_roles=['runtime_fact_adapter'], segment_weights=weights,
        ordinal_tasks=[dict(item_id=name, case_id=draft.segment_id, dimension=name) for name in weights],
        hard_gate_tasks=[dict(item_id=gate, case_id=draft.segment_id, gate=gate) for gate in gates],
        state_tasks=[dict(item_id=draft.segment_id, case_id=draft.segment_id,
                         ordinal_item_ids=list(weights), hard_gate_item_ids=gates)])
    context = {key: getattr(draft, key) for key in ('demand_direct_evidence', 'supply_direct_evidence',
        'counter_evidence_search_complete', 'mandatory_conflict', 'independent_supply_constraint_count',
        'key_source_quality_high')}
    pack = dict(cases=[dict(case_id=draft.segment_id, evidence_ids=sorted(evidence_ids), state_context=context)])
    result = dict(**metadata, annotator_id='v161-runtime', annotator_role='runtime_fact_adapter',
        independent_work_attestation=True, ordinal=[dict(item_id=item.dimension,
            evidence_state=item.evidence_state, bound_type=item.bound_type, rating_min=item.rating_min,
            rating_max=item.rating_max, bound_basis=asdict(item.bound_basis), primary_evidence_ids=item.evidence_ids,
            bound_derivation='unresolved' if item.evidence_state == 'unknown' else 'rule_derived',
            rationale=item.rationale or 'Insufficient original-source evidence; preserve unknown bounds.')
            for item in draft.dimensions])
    with TemporaryDirectory(prefix='theme-v161-state-') as directory:
        paths = [Path(directory)/name for name in ('task.json', 'pack.json', 'ordinal.json', 'computed.json')]
        for path, data in zip(paths, (task, pack, result)):
            path.write_text(json.dumps(data), encoding='utf-8')
        subprocess.run(['node', str(ROOT/'tools/theme-chokepoint/materialize-calibration.mjs'),
            *map(str, paths), '--from-ordinals', '--runtime-evidence-gaps'],
            check=True, capture_output=True, text=True, timeout=30)
        state = json.loads(paths[-1].read_text(encoding='utf-8'))['state'][0]
    metrics = state['derived_metrics']
    return SegmentAssessment(segment_id=draft.segment_id, contract_version=contract_version,
        dimensions=draft.dimensions, **metrics,
        conflicted_weight_share=sum(weights[item.dimension] for item in draft.dimensions if item.evidence_state == 'conflicted')/100,
        unknown_weight_share=sum(weights[item.dimension] for item in draft.dimensions if item.evidence_state == 'unknown')/100,
        stale_weight_share=sum(weights[item.dimension] for item in draft.dimensions if item.stale)/100,
        primary_state=state['primary_state'], missing_material_fields=draft.missing_material_fields,
        relief_horizon=draft.relief_horizon, eligible=state['primary_state'] is not None,
        achieved_gates=tuple(state['achieved_hard_gates']), withheld_reason=state['withheld_reason'],
        relief_assessment=draft.relief_assessment, critic_receipt=draft.critic_receipt)


class V161SegmentScorer:
    def __init__(self, extractor):
        self.extractor = extractor

    def assess(self, node, claims, evidence_cards, *, request, contract_version):
        if contract_version != RUNTIME_VERSION:
            raise ValueError('v161 runtime requires its explicit staging contract')
        from event_collector.theme_chokepoint.stage3 import _validate_segment_draft_evidence
        empty = SegmentScoreDraft(node.node_id, tuple(
            DimensionRatingDraft(name, 0, 4, 'unknown', 'none', BoundBasis(), (), False)
            for name in DIMENSIONS), tuple(DIMENSIONS), False, False, False, False, None)
        _validate_segment_draft_evidence(empty, node=node, claims=claims,
                                        evidence_cards=evidence_cards, request=request)
        claims_by_id = {claim.claim_id: claim for claim in claims}
        evidence_cards = tuple(card for card in evidence_cards if card.scoring_eligible
            and not card.source_ambiguity and card.scoring_use != 'context_only'
            and claims_by_id[card.claim_id].scoring_eligible
            and claims_by_id[card.claim_id].scoring_use != 'context_only')
        if not evidence_cards:
            return empty
        if len({card.assessment_scope for card in evidence_cards}) != 1:
            raise ValueError('v161 runtime requires one atomic Scope per segment')
        scope = json.loads(json.dumps(asdict(evidence_cards[0].assessment_scope), default=str))
        case_id = sha256(json.dumps(scope, sort_keys=True, default=str).encode()).hexdigest()
        case = dict(case_id=case_id, assessment_scope=scope,
                    evidence_ids=[card.evidence_id for card in evidence_cards])
        evidence = tuple(json.loads(json.dumps(asdict(card), default=str)) for card in evidence_cards)
        extracted = self.extractor.extract(case=case, evidence=evidence)
        if (extracted.case_id != case_id or extracted.prompt_sha256 != V161_FACT_PROMPT_SHA256
                or extracted.schema_sha256 != V161_FACT_SCHEMA_SHA256):
            raise ValueError('v161 runtime extraction identity mismatch')
        facts, ratings = _decode_and_score_v161(json.dumps(extracted.facts), set(case['evidence_ids']))
        cards = {card.evidence_id: card for card in evidence_cards}
        dimensions = []
        for name in DIMENSIONS:
            low, high = ratings[name]
            unknown = (low, high) == (0, 4)
            kind = ('none' if unknown else 'exact' if low == high else 'lower_bound' if high == 4
                    else 'upper_bound' if low == 0 else 'interval')
            ids = () if unknown else tuple(facts[name]['evidence_ids'])
            basis = BoundBasis(
                floor_anchor=low if kind in ('exact', 'lower_bound', 'interval') else None,
                ceiling_anchor=high if kind in ('exact', 'upper_bound', 'interval') else None,
                exact_basis=('natural_cap' if high == 4 else 'contract_exclusivity') if kind == 'exact' else None,
                unresolved_higher_anchors=tuple(range(low + 1, 5)) if kind == 'lower_bound' else (),
                excluded_higher_anchors=tuple(range(high + 1, 5)) if kind in ('exact', 'upper_bound', 'interval') else (),
            )
            conditions = tuple(AnchorConditionResult(
                condition_id=f'{name}:{kind}:{side}', condition_type=side, evidence_state='supported',
                evidence_ids=ids, decisive_claim_ids=tuple(cards[item].claim_id for item in ids),
                anchor=anchor, excluded_higher_anchors=basis.excluded_higher_anchors if side == 'ceiling' else (),
            ) for side, anchor in (('floor', basis.floor_anchor), ('ceiling', basis.ceiling_anchor))
                if anchor is not None)
            dimensions.append(DimensionRatingDraft(name, low, high, 'unknown' if unknown else 'supported',
                kind, basis, ids, False,
                rationale='Deterministic v1.6.1 mapping of source-bound facts.',
                anchor_conditions=conditions, decisive_evidence_ids=ids))
        return SegmentScoreDraft(node.node_id, tuple(dimensions),
            tuple(item.dimension for item in dimensions if item.evidence_state == 'unknown'),
            False, False, False, False, None)
