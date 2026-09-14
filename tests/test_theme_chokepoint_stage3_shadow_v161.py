from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from event_collector.theme_chokepoint.providers.facts_v161 import DeepSeekV161SegmentFactExtractor
from event_collector.theme_chokepoint.repository import ThemeChokepointRepository
from event_collector.theme_chokepoint.validation_v161 import DIMENSIONS
from tests.test_theme_chokepoint_stage3 import (
    ProgressiveScorer, RecordingAcquirer, _all_dimension_candidates,
    _controlled_loop, _seed_run,
)


class FactCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        request = kwargs['messages'][1]['content']
        case = json.loads(request.split('CASE_CONTEXT_JSON:\n')[1].split('\n\nCASE_BOUND_EVIDENCE_JSON:')[0])
        ids = case['evidence_ids']
        facts = {'schema_version': 'theme-chokepoint-segment-facts-v1.6.1'}
        facts.update({name: {'unresolved': True, 'evidence_ids': [ids[0]]} for name in DIMENSIONS})
        facts['downstream_criticality'] = {
            'unresolved': False, 'scope_dependency_verified': True,
            'structural_hard_block': 'unknown', 'explicit_no_impact': False,
            'cohorts': [], 'evidence_ids': [ids[0]],
        }
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(facts)))])


def setup_shadow(tmp_path):
    repository = ThemeChokepointRepository(tmp_path / 'canonical.db')
    request = _seed_run(repository)
    loop = _controlled_loop(repository, RecordingAcquirer(_all_dimension_candidates()), ProgressiveScorer())
    baseline = loop.run(request.run_id)
    completions = FactCompletions()
    extractor = DeepSeekV161SegmentFactExtractor(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model='controlled-shadow-fixture',
    )
    return repository, request, loop, baseline, extractor, completions


def test_stage3_shadow_persists_scoped_facts_without_changing_canonical_result(tmp_path):
    """SELECT INVARIANT: persisted Stage 3 evidence can be scored in isolated v161 shadow."""
    repo, request, loop, baseline, extractor, calls = setup_shadow(tmp_path)
    before = repo.get_run(request.run_id)
    result = loop.run_v161_shadow(request.run_id, facts_only=True, extractor=extractor, artifact_root=tmp_path/'shadow', shadow_id='trial-1')
    assert result is not None
    assert result['mode'] == 'shadow'
    segment = result['segments'][0]
    assert segment['ratings']['downstream_criticality'] == [4, 4]
    assert segment['assessment_scope']['segment_id'] == 'node-segment'
    assert {item['claim_id'] for item in segment['evidence']} == {item.claim_id for item in baseline.claims}
    assert segment['primary_state'] is None
    assert segment['state_status'] == 'withheld_pending_v161_critic'
    receipts = list((tmp_path/'shadow').glob('*.json'))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text(encoding='utf-8')) == result
    assert repo.get_stage3_result(request.run_id) == baseline
    assert repo.get_run(request.run_id) == before
    assert len(calls.calls) == 1


@pytest.mark.parametrize('corruption', ['quote', 'scope', 'claim', 'ineligible', 'duplicate', 'empty'])
def test_shadow_rejects_broken_source_or_claim_binding_before_inference(tmp_path, monkeypatch, corruption):
    """SELECT INVARIANT: replay must validate loaded evidence binding before provider I/O."""
    repo, request, loop, baseline, extractor, calls = setup_shadow(tmp_path)
    card = baseline.evidence_cards[0]
    if corruption == 'quote':
        card = replace(card, exact_quote='A forged quotation absent from the original source.')
    elif corruption == 'scope':
        card = replace(card, assessment_scope=replace(card.assessment_scope, geography='another market'))
    elif corruption == 'claim':
        card = replace(card, claim_id=baseline.claims[1].claim_id)
    elif corruption == 'ineligible':
        card = replace(card, scoring_eligible=False)
    corrupted = replace(baseline, evidence_cards=(card, *baseline.evidence_cards[1:]))
    if corruption == 'duplicate':
        corrupted = replace(baseline, evidence_cards=(*baseline.evidence_cards, card))
    elif corruption == 'empty':
        corrupted = replace(baseline, evidence_cards=())
    monkeypatch.setattr(repo, 'get_stage3_result', lambda run_id: corrupted)
    with pytest.raises(ValueError, match='binding'):
        loop.run_v161_shadow(request.run_id, facts_only=True, extractor=extractor, artifact_root=tmp_path/'shadow', shadow_id='invalid')
    assert not calls.calls
    assert not list((tmp_path/'shadow').glob('*.json'))


def test_shadow_retry_reuses_persisted_result_and_rejects_changed_model(tmp_path):
    """SELECT INVARIANT: one shadow ID pins its inputs; retry does not spend again."""
    repo, request, loop, baseline, extractor, calls = setup_shadow(tmp_path)
    kwargs = dict(extractor=extractor, artifact_root=tmp_path/'shadow', shadow_id='retry')
    first = loop.run_v161_shadow(request.run_id, facts_only=True, **kwargs)
    assert loop.run_v161_shadow(request.run_id, facts_only=True, **kwargs) == first
    assert len(calls.calls) == 1
    extractor.model = 'different-model'
    with pytest.raises(ValueError, match='identity'):
        loop.run_v161_shadow(request.run_id, facts_only=True, **kwargs)
    assert len(calls.calls) == 1


def test_shadow_recomputes_adapter_ratings_from_facts(tmp_path, monkeypatch):
    """SELECT INVARIANT: adapter ratings never override deterministic fact scoring."""
    repo, request, loop, baseline, extractor, calls = setup_shadow(tmp_path)
    original = extractor.extract
    def altered(**kwargs):
        extracted = original(**kwargs)
        return replace(extracted, ratings={name: (4, 4) for name in DIMENSIONS})
    monkeypatch.setattr(extractor, 'extract', altered)
    result = loop.run_v161_shadow(request.run_id, facts_only=True, extractor=extractor, artifact_root=tmp_path/'shadow', shadow_id='adapter')
    assert result['segments'][0]['ratings']['demand_pressure'] == [0, 4]
    assert result['segments'][0]['score_min'] == 20


def test_shadow_rejects_wrong_case_from_adapter_without_publishing(tmp_path, monkeypatch):
    """SELECT INVARIANT: a response must belong to this exact scoped extraction."""
    repo, request, loop, baseline, extractor, calls = setup_shadow(tmp_path)
    original = extractor.extract
    monkeypatch.setattr(extractor, 'extract', lambda **kwargs: replace(original(**kwargs), case_id='wrong-case'))
    with pytest.raises(ValueError, match='extraction identity'):
        loop.run_v161_shadow(request.run_id, facts_only=True, extractor=extractor, artifact_root=tmp_path/'shadow', shadow_id='wrong-case')
    assert not list((tmp_path/'shadow').glob('*.json'))


def test_shadow_refuses_corrupted_saved_receipt(tmp_path):
    """SELECT INVARIANT: persisted replay output is checked before reuse."""
    repo, request, loop, baseline, extractor, calls = setup_shadow(tmp_path)
    kwargs = dict(extractor=extractor, artifact_root=tmp_path/'shadow', shadow_id='corrupt')
    loop.run_v161_shadow(request.run_id, facts_only=True, **kwargs)
    receipt = next((tmp_path/'shadow').glob('*.json'))
    payload = json.loads(receipt.read_text(encoding='utf-8'))
    payload['segments'][0]['score_min'] = 100
    receipt.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError, match='checksum'):
        loop.run_v161_shadow(request.run_id, facts_only=True, **kwargs)
    assert len(calls.calls) == 1


def test_shadow_write_failure_does_not_publish_partial_receipt(tmp_path, monkeypatch):
    """SELECT INVARIANT: interrupted persistence leaves no completed JSON receipt."""
    repo, request, loop, baseline, extractor, calls = setup_shadow(tmp_path)
    output = tmp_path/'shadow'
    original = Path.open
    class BrokenWriter:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def write(self, content):
            self.stream.write(content[:20])
            raise OSError('simulated disk failure')
    def open_with_failure(path, mode='r', *args, **kwargs):
        stream = original(path, mode, *args, **kwargs)
        return BrokenWriter(stream) if path.parent == output and mode in ('w', 'x') else stream
    monkeypatch.setattr(Path, 'open', open_with_failure)
    with pytest.raises(OSError, match='disk failure'):
        loop.run_v161_shadow(request.run_id, facts_only=True, extractor=extractor, artifact_root=output, shadow_id='disk-failure')
    assert not list(output.glob('*.json'))
    assert repo.get_stage3_result(request.run_id) == baseline
