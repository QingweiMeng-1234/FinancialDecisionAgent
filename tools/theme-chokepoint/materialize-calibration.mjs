import { validateSubmission } from '../../outputs/theme-chokepoint-v1.6-r7-evaluation-20260823-001/post-adjudication-blind-package/calibration/semantic-validator-v1.6-eval.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export function completeCalibrationFromOrdinals({ task, pack, result, runtimeEvidenceGaps = false }) {
  const computed = structuredClone(result);
  computed.hard_gate = task.hard_gate_tasks.map(item => ({
    item_id: item.item_id, label: 'unknown',
    evidence_ids: pack.cases.find(row => row.case_id === item.case_id).evidence_ids,
    rationale: 'Derived by the frozen validator from ordinal evidence and case context.',
  }));
  computed.state = task.state_tasks.map(item => ({
    item_id: item.item_id, primary_state: null, derived_metrics: {},
    achieved_hard_gates: [], withheld_reason: 'Not yet derived.',
    rationale: 'Derived by the frozen validator from ordinal evidence and case context.',
  }));
  const report = validateSubmission({ task, pack, result: computed });
  for (const item of computed.state) {
    const derived = report.derived[item.item_id];
    item.derived_metrics = derived.metrics;
    item.primary_state = derived.primary_state;
    item.achieved_hard_gates = Object.entries(derived.hard_gates)
      .filter(([, label]) => label === 'pass').map(([id]) => id);
    item.withheld_reason = derived.primary_state === null ? 'Frozen state eligibility is not established.' : null;
    for (const gate of computed.hard_gate) {
      if (gate.item_id in derived.hard_gates) gate.label = derived.hard_gates[gate.item_id];
    }
  }
  const completed = validateSubmission({ task, pack, result: computed });
  // Calibration annotations require citations even for unknowns. A live
  // acquisition gap has no citation: permit only that exact empty/unknown case.
  const permittedGaps = new Set();
  if (runtimeEvidenceGaps) {
    for (const item of computed.ordinal) {
      if (item.evidence_state === 'unknown' && Array.isArray(item.primary_evidence_ids)
          && item.primary_evidence_ids.length === 0) permittedGaps.add(`${item.item_id}:evidence_ids`);
    }
    for (const item of computed.hard_gate) {
      if (item.label !== 'pass' && Array.isArray(item.evidence_ids) && item.evidence_ids.length === 0)
        permittedGaps.add(`${item.item_id}:evidence_ids`);
    }
  }
  const errors = completed.errors.filter(error => !permittedGaps.has(error));
  if (errors.length) throw new Error(errors.join('\n'));
  return computed;
}

export function materializeCalibration({ task, pack, result }) {
  const report = validateSubmission({ task, pack, result });
  const errors = report.errors.filter(error => !error.includes(':derived_metric_mismatch:'));
  if (errors.length) throw new Error(errors.join('\n'));
  const computed = structuredClone(result);
  for (const item of computed.state) {
    item.derived_metrics = report.derived[item.item_id].metrics;
  }
  return computed;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [taskPath, packPath, rawPath, outputPath] = process.argv.slice(2);
  if (!outputPath) throw new Error('usage: materialize-calibration.mjs task.json pack.json raw.json computed.json');
  const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
  const materialize = process.argv.includes('--from-ordinals') ? completeCalibrationFromOrdinals : materializeCalibration;
  const computed = materialize({ task: read(taskPath), pack: read(packPath), result: read(rawPath),
    runtimeEvidenceGaps: process.argv.includes('--runtime-evidence-gaps') });
  fs.writeFileSync(outputPath, JSON.stringify(computed, null, 2) + '\n', { encoding: 'utf8', flag: 'wx' });
}
