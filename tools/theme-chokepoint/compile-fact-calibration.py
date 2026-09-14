"""One-off bridge from preserved v1.6.1 facts to frozen calibration validation."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from event_collector.theme_chokepoint.providers.facts_v161 import _decode_and_score_v161

run = Path(sys.argv[1]).resolve()
pack_dir = ROOT / 'outputs/theme-chokepoint-v1.6-r7-evaluation-20260823-001/post-adjudication-blind-package/calibration'
read = lambda p: json.loads(p.read_text(encoding='utf-8'))
task = read(pack_dir / 'annotation-task-v1.6.1.json')
pack = read(pack_dir / 'evidence-pack-v1.6.json')
manifest = read(run / 'manifest.json')
assert manifest['status'] == 'completed' and manifest['inference_mode'] == 'facts_v161'
raw = read(run / 'response.json')
assert sorted(row['case_id'] for row in raw['cases']) == sorted(row['case_id'] for row in pack['cases'])
scored = {}
for row in raw['cases']:
    case = next(case for case in pack['cases'] if case['case_id'] == row['case_id'])
    facts, ratings = _decode_and_score_v161(json.dumps(row['facts']), set(case['evidence_ids']))
    scored[row['case_id']] = {'facts': facts, 'ratings': ratings, 'reasoning': row['reasoning']}

result = {key: task[key] for key in ('task_id', 'contract_id', 'evidence_pack_id')}
result.update(annotator_id=manifest['requested_model'] + '-facts-v161',
              annotator_role='blind_model_annotator', independent_work_attestation=True,
              input_hashes=manifest['input_hashes'], ordinal=[])
for item in task['ordinal_tasks']:
    row = scored[item['case_id']]
    low, high = row['ratings'][item['dimension']]
    unknown = (low, high) == (0, 4)
    kind = 'none' if unknown else 'exact' if low == high else 'lower_bound' if high == 4 else 'upper_bound' if low == 0 else 'interval'
    basis = {
        'floor_anchor': low if kind in ('exact', 'lower_bound', 'interval') else None,
        'ceiling_anchor': high if kind in ('exact', 'upper_bound', 'interval') else None,
        'exact_basis': ('natural_cap' if high == 4 else 'contract_exclusivity') if kind == 'exact' else None,
        'unresolved_higher_anchors': list(range(low + 1, 5)) if kind == 'lower_bound' else [],
        'excluded_higher_anchors': list(range(high + 1, 5)) if kind in ('exact', 'upper_bound', 'interval') else [],
    }
    result['ordinal'].append({
        'item_id': item['item_id'], 'evidence_state': 'unknown' if unknown else 'supported',
        'bound_type': kind, 'rating_min': low, 'rating_max': high, 'bound_basis': basis,
        'primary_evidence_ids': row['facts'][item['dimension']]['evidence_ids'],
        'bound_derivation': 'unresolved' if unknown else 'rule_derived',
        'rationale': row['reasoning'][item['dimension']] + ' Bounds computed by score_v161_segment_facts.',
    })
for name, value in [('scored-facts.json', scored), ('ordinal-response.json', result)]:
    with (run / name).open('x', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
subprocess.run(['node', str(ROOT / 'tools/theme-chokepoint/materialize-calibration.mjs'),
                str(pack_dir / 'annotation-task-v1.6.1.json'), str(pack_dir / 'evidence-pack-v1.6.json'),
                str(run / 'ordinal-response.json'), str(run / 'computed-response.json'), '--from-ordinals'],
               cwd=ROOT, check=True)
print('Validated facts and deterministic submission: ' + str(run / 'computed-response.json'))
