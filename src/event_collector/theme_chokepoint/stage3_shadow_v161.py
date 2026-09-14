"""Opt-in v1.6.1 fact replay of persisted Stage 3 evidence; never a canonical result."""

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from event_collector.theme_chokepoint.scoring_v16 import V16ScoringContract
from event_collector.theme_chokepoint import scoring_v16, validation_v161
from event_collector.theme_chokepoint import shadow_counter_v161
from event_collector.theme_chokepoint.providers.facts_v161 import (
    V161_FACT_PROMPT_SHA256, V161_FACT_SCHEMA_SHA256, _decode_and_score_v161,
)


CONTRACT_SHA256 = 'c736ffd60eb0a905d93ff7707292e913dff4a5ca3a5c5a6699d53e9e4699e046'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=lambda item: item.isoformat())


def run_shadow(repository, run_id, *, extractor, artifact_root, shadow_id,
               counter_executor=None, facts_only=False):
    if not facts_only and counter_executor is None:
        raise ValueError('full shadow requires a counter executor; facts_only must be explicit')
    if not facts_only and (not isinstance(getattr(counter_executor, 'identity', None), str)
                           or not counter_executor.identity.strip()):
        raise ValueError('counter executor identity must be a stable non-empty string')
    baseline = repository.get_stage3_result(run_id)
    if (not baseline.evidence_cards
            or len({card.evidence_id for card in baseline.evidence_cards}) != len(baseline.evidence_cards)):
        raise ValueError('Stage 3 shadow requires non-empty unique evidence binding')
    root = Path(__file__).resolve().parents[3]
    contract = V16ScoringContract(
        root/'tools/theme-chokepoint/semantic-task-contract-v1.6.json',
        expected_sha256=CONTRACT_SHA256,
    )
    baseline_sha256 = sha256(_json(asdict(baseline)).encode()).hexdigest()
    identity = {
        'baseline_sha256': baseline_sha256, 'contract_sha256': contract.sha256,
        'graph_sha256': sha256(_json(asdict(repository.get_supply_chain_graph(run_id))).encode()).hexdigest(),
        'model': extractor.model, 'prompt_sha256': extractor.prompt_sha256,
        'schema_sha256': extractor.schema_sha256,
        'counter_executor': getattr(counter_executor, 'identity', None),
        'facts_only': facts_only,
        'code_sha256': {Path(file).name: sha256(Path(file).read_bytes()).hexdigest()
                        for file in (__file__, scoring_v16.__file__, validation_v161.__file__,
                                     shadow_counter_v161.__file__)},
    }
    output = Path(artifact_root)
    filename = sha256(_json([run_id, shadow_id]).encode()).hexdigest() + '.json'
    path = output/filename
    if path.exists():
        cached = json.loads(path.read_text(encoding='utf-8'))
        body = {key: value for key, value in cached.items() if key != 'receipt_sha256'}
        if cached.get('receipt_sha256') != sha256(_json(body).encode()).hexdigest():
            raise ValueError('shadow receipt checksum mismatch')
        if cached['identity'] != identity:
            raise ValueError('shadow identity changed; use a new shadow_id')
        return cached
    claims = {item.claim_id: item for item in baseline.claims}
    snapshots = {item.article_id: item for item in baseline.source_snapshots}
    groups = {}
    for card in baseline.evidence_cards:
        claim = claims.get(card.claim_id)
        snapshot = snapshots.get(card.article_id)
        if (claim is None or snapshot is None or card.assessment_scope is None
                or not card.scoring_eligible or not claim.scoring_eligible
                or card.source_ambiguity or claim.source_ambiguity
                or card.assessment_scope.company_id is not None
                or card.assessment_scope != claim.assessment_scope
                or claim.node_id != card.assessment_scope.segment_id
                or card.evidence_id not in claim.evidence_ids
                or snapshot.canonical_url != card.canonical_url
                or card.content_hash != snapshot.content_hash
                or snapshot.content_hash != 'sha256:' + sha256(snapshot.original_text.encode()).hexdigest()
                or not 0 <= card.quote_start < card.quote_end <= len(snapshot.original_text)
                or snapshot.original_text[card.quote_start:card.quote_end] != card.exact_quote):
            raise ValueError('Stage 3 shadow evidence/source/Claim binding is invalid')
        scope = json.loads(_json(asdict(card.assessment_scope)))
        key = _json(scope)
        group = groups.setdefault(key, {'scope': scope, 'evidence': [], 'claims': {}})
        evidence = asdict(card)
        evidence['quote_context'] = snapshots[card.article_id].original_text
        group['evidence'].append(evidence)
        group['claims'][claim.claim_id] = asdict(claim)

    segments = []
    for key, group in sorted(groups.items()):
        case_id = 'scope-' + sha256(key.encode()).hexdigest()
        case = {'case_id': case_id, 'assessment_scope': group['scope'],
                'evidence_ids': [item['evidence_id'] for item in group['evidence']],
                'state_context': {}}
        extraction = extractor.extract(case=case, evidence=tuple(json.loads(_json(group['evidence']))))
        if (extraction.case_id != case_id or extraction.model != identity['model']
                or extraction.prompt_sha256 != V161_FACT_PROMPT_SHA256
                or extraction.schema_sha256 != V161_FACT_SCHEMA_SHA256):
            raise ValueError('shadow extraction identity does not match the v1.6.1 request')
        facts, ratings = _decode_and_score_v161(_json(extraction.facts), set(case['evidence_ids']))
        segments.append({
            'case_id': case_id, 'assessment_scope': group['scope'],
            'claims': list(group['claims'].values()), 'evidence': group['evidence'],
            'extraction': asdict(extraction), 'validated_facts': facts, 'ratings': ratings,
            'score_min': sum(contract.segment_weights[name] * bounds[0] / 4 for name, bounds in ratings.items()),
            'score_max': sum(contract.segment_weights[name] * bounds[1] / 4 for name, bounds in ratings.items()),
            # Only explicit facts-only mode withholds fresh counter-search/state evaluation.
            'primary_state': None, 'state_status': 'withheld_pending_v161_critic',
        })
        if counter_executor is not None and not facts_only:
            segments[-1].update(shadow_counter_v161.evaluate(
                repository=repository, run_id=run_id, shadow_id=shadow_id,
                output=output, root=root, contract=contract, case=case,
                facts=facts, ratings=ratings, baseline=baseline,
                executor=counter_executor,
            ))
    result = {
        'schema_version': 'theme-chokepoint-stage3-shadow-v1.6.1',
        'mode': 'shadow', 'production_eligible': False, 'run_id': run_id,
        'shadow_id': shadow_id, 'created_at': datetime.now(timezone.utc),
        'baseline_contract_id': baseline.executable_contract_id,
        'baseline_sha256': baseline_sha256, 'identity': identity,
        'scoring_contract_sha256': contract.sha256, 'segments': segments,
    }
    result = json.loads(_json(result))
    result['receipt_sha256'] = sha256(_json(result).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / (filename + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            stream.write(_json(result) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        # Publishing a complete file is atomic and refuses an existing receipt.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return result
