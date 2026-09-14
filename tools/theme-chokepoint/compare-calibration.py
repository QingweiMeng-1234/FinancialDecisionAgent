"""One-off result report using frozen validators and current threshold policy."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(sys.argv[1]).resolve()
RESPONSE = OUT / ('computed-response.json' if '--computed' in sys.argv[2:] else 'response.json')
PREFIX = 'computed-' if RESPONSE.name == 'computed-response.json' else ''
BASE=ROOT/'outputs/theme-chokepoint-v1.6-r7-evaluation-20260823-001'
PACK=BASE/'post-adjudication-blind-package/calibration'
REFERENCE=BASE/'adjudication/adjudicated-calibration-result-v1.6.1.json'
POLICY=ROOT/'config/theme_chokepoint_automated_freeze_thresholds_v4.json'
PREFIX += 'v4-'
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def save(name,value): (OUT/(PREFIX+name)).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
response=read(RESPONSE); reference=read(REFERENCE); task=read(PACK/'annotation-task-v1.6.1.json')
command=['node',str(PACK/'validate-submission-v1.6.1.mjs'),str(PACK/'annotation-task-v1.6.1.json'),str(PACK/'evidence-pack-v1.6.json'),str(RESPONSE)]
v=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',cwd=ROOT)
validation=json.loads(v.stdout);save('validation.json',validation)
comparison_path=OUT/(PREFIX+'agreement-legacy-formula.json')
c=subprocess.run(['node',str(ROOT/'tools/theme-chokepoint/evaluation/calculate-agreement-v1.5.mjs'),str(REFERENCE),str(RESPONSE),str(comparison_path)],capture_output=True,text=True,encoding='utf-8',cwd=ROOT,check=True)
legacy=read(comparison_path);threshold=read(POLICY)['thresholds']
groups={'ordinal':task['ordinal_tasks'],'hard_gate':task['hard_gate_tasks'],'state':task['state_tasks']}
coverage={g: sorted(x['item_id'] for x in response[g])==sorted(x['item_id'] for x in definitions) for g,definitions in groups.items()}
def keyed(items):return {x['item_id']:x for x in items}
diffs={}
fields={'ordinal':['evidence_state','bound_type','rating_min','rating_max','bound_derivation'],'hard_gate':['label'],'state':['primary_state']}
for group,keys in fields.items():
    a,b=keyed(reference[group]),keyed(response[group]); diffs[group]=[]
    definitions={x['item_id']:x for x in groups[group]}
    for key in a.keys()&b.keys():
        changes={f:{'reference':a[key].get(f),'model':b[key].get(f)} for f in keys if a[key].get(f)!=b[key].get(f)}
        if changes:diffs[group].append({'item_id':key,'task':definitions[key],'changes':changes,'reference_rationale':a[key].get('rationale'),'model_rationale':b[key].get('rationale')})
    diffs[group].sort(key=lambda x:x['item_id'])
risk={'candidate_chokepoint','strong_candidate_chokepoint'}
state_ref,state_model=keyed(reference['state']),keyed(response['state'])
upgrades=[k for k in state_ref if state_ref[k].get('primary_state') not in risk and state_model.get(k,{}).get('primary_state') in risk]
manifest=read(OUT/'manifest.json')
no_tools=not any(t not in ('agent_message','reasoning','') for t in manifest.get('item_types',[]))
gates={'schema_and_semantics':validation['valid'],'all_expected_items':all(coverage.values()),'no_tools_used':no_tools,'hard_gate_agreement':legacy['hard_gate_agreement']>=threshold['hard_gate_agreement_min'],'final_state_agreement':legacy['final_state_agreement']>=threshold['final_state_agreement_min'],'kappa':legacy['counts']['exact_exact_pairs']>=threshold['exact_exact_pairs_min'] and legacy['linear_weighted_kappa'] is not None and legacy['linear_weighted_kappa']>=threshold['linear_weighted_kappa_min'],'high_risk_boundary':len(legacy['high_risk_state_disagreement_item_ids'])<=threshold['high_risk_boundary_disagreements_max']}
report={'generated_at':datetime.now(timezone.utc).isoformat(),'status':'pass' if all(gates.values()) else 'fail','scope':'new isolated model inference on frozen calibration; not unseen holdout or production end-to-end','requested_model':manifest['requested_model'],'reasoning_effort':manifest['reasoning_effort'],'reference_sha256':sha(REFERENCE),'response_sha256':sha(OUT/'response.json'),'policy_sha256':sha(POLICY),'thresholds':threshold,'gates':gates,'counts':legacy['counts'],'ordinal_diagnostics':legacy['ordinal_diagnostics'],'linear_weighted_kappa':legacy['linear_weighted_kappa'],'hard_gate_agreement':legacy['hard_gate_agreement'],'final_state_agreement':legacy['final_state_agreement'],'high_risk_disagreements':legacy['high_risk_state_disagreement_item_ids'],'model_upgrades_across_high_risk_boundary':upgrades,'differences':diffs,'validation_errors':validation['errors'],'usage_events':manifest['completion_events'],'item_types':manifest['item_types'],'money_cost':None,'proof_limits':['Fixed-reference agreement, not human inter-rater reliability.','The legacy formula report contains historical gates; only current v3 gates in this report determine pass/fail.','No answer-conditioned repair; the first successful model response is scored unchanged.','Source-ID and mechanical validation do not prove semantic faithfulness or absence of invented claims.','The calibration includes former holdout material; no unseen-generalization claim.']}
report['evaluated_response'] = RESPONSE.name
report['policy_id'] = read(POLICY)['policy_id']
report['evaluation_kind'] = 'policy_evaluation_of_preserved_response'
report['proof_limits'][1] = 'Only the active policy identified by policy_id and policy_sha256 in this report determines pass/fail; historical reports remain unchanged.'
report['response_sha256'] = sha(RESPONSE)
report['evaluated_response_sha256'] = sha(RESPONSE)
report['raw_model_response_sha256'] = sha(OUT/'response.json')
report['arithmetic_materialized'] = RESPONSE.name == 'computed-response.json'
report['proof_limits'][2] = 'The prompt procedure was tuned using known calibration disagreements. Each call omits the reference; this is calibration development, not an untouched blind evaluation. Raw model judgments are preserved; computed mode replaces only deterministic derived metrics.'
fact_mode = manifest.get('inference_mode') == 'facts_v161'
report['inference_mode'] = manifest.get('inference_mode', 'direct_scoring')
if fact_mode:
    report['arithmetic_materialized'] = False
    report['proof_limits'][2] = 'The existing v1.6.1 fact prompt is used unchanged. Model facts are validated and scored by existing code; frozen formulas derive gates and states. This calibrates the fact path and is not comparable to raw model-authored scoring as the same system.'
save('comparison.json',report)
lines=['# Theme-Research 真实模型黄金集评测','',f"- 模型：{manifest['requested_model']} / {manifest['reasoning_effort']}，CLI {manifest['cli_version']}。",f"- 结果：**{report['status'].upper()}**；24 项维度、12 项门槛、4 项最终状态。",'- 固定 12 个来源、13 条证据；新调用不含参考答案，不作答案驱动修补。','', '| 指标 | 本次 | 当前要求 |','| --- | ---: | ---: |',f"| 结构与语义校验 | {validation['valid']} | true |",f"| Hard Gate 一致率 | {legacy['hard_gate_agreement']:.1%} | {threshold['hard_gate_agreement_min']:.0%} |",f"| 最终状态一致率 | {legacy['final_state_agreement']:.1%} | {threshold['final_state_agreement_min']:.0%} |",f"| 精确评分配对数 | {legacy['counts']['exact_exact_pairs']} | ≥{threshold['exact_exact_pairs_min']} |",f"| 线性加权 Kappa | {legacy['linear_weighted_kappa']} | ≥{threshold['linear_weighted_kappa_min']} |",f"| 高风险边界分歧 | {len(legacy['high_risk_state_disagreement_item_ids'])} | ≤{threshold['high_risk_boundary_disagreements_max']} |",f"| 完整评分四元组一致率 | {legacy['ordinal_diagnostics']['full_tuple_agreement']:.1%} | 诊断项 |",'', '## 最终状态','', '| 案例 | 参考 | 新模型 |','| --- | --- | --- |']
lines[4] = '- 固定 12 个来源、13 条证据；模型调用不含参考答案。流程提示已根据已知校准分歧调整，属于校准开发。'
lines.insert(5, f"- 评估文件：{RESPONSE.name}；" + ('仅派生指标由冻结公式重算，评分、门槛和状态保持原始输出。' if RESPONSE.name == 'computed-response.json' else '模型原始输出，未修改。'))
if fact_mode:
    lines[4] = '- 固定 12 个来源、13 条证据；使用仓库原有 v1.6.1 事实提取提示，调用不含参考答案。'
    lines[5] = '- 评估文件：computed-response.json；原始事实保留在 response.json，维度评分、门槛和最终状态均由现有代码推导。'
lines.insert(6, f"- 验收策略：{report['policy_id']}；对已保存结果复验，没有新增模型调用。")
for key,a in state_ref.items():lines.append(f"| {key} | {a.get('primary_state') or 'withheld'} | {state_model.get(key,{}).get('primary_state') or 'withheld'} |")
lines+=['','## 校验问题','']+[f'- {e}' for e in validation['errors']]
if not validation['errors']:lines+=['无机械校验错误。']
lines+=['','## 评分差异','']
for d in diffs['ordinal']:
    lines += [f"### {d['item_id']} — {d['task']['dimension']}",'',json.dumps(d['changes'],ensure_ascii=False), '', '模型理由：'+str(d['model_rationale']),'','参考理由：'+str(d['reference_rationale']),'']
lines+=['## 证据边界','','这是固定校准集评测，不是未见测试集、实盘研究或生产全链路验证。Kappa 仅基于双方均为 exact 的配对，应结合配对数量理解。旧 CLI 的版本拒绝保留为独立失败记录；没有更换模型或覆盖原始输出。没有调用外部知识检索。引用真实性仍需逐条语义审查。']
(OUT/(PREFIX+'report.md')).write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ('differences','proof_limits')},ensure_ascii=False,indent=2))
