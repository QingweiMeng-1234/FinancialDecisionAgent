import assert from 'node:assert/strict';
import test from 'node:test';
import { materializeCalibration, completeCalibrationFromOrdinals } from './materialize-calibration.mjs';

function fixture() {
  const dimensions = ['demand_pressure', 'downstream_criticality', 'effective_supply_concentration', 'qualification_barrier', 'capacity_inelasticity', 'substitute_weakness'];
  const task = {
    task_id: 'synthetic', contract_id: 'synthetic', evidence_pack_id: 'pack',
    allowed_annotator_roles: ['blind_model_annotator'],
    segment_weights: Object.fromEntries(dimensions.map((d, i) => [d, [15, 20, 15, 15, 15, 20][i]])),
    ordinal_tasks: dimensions.map((dimension, i) => ({ item_id: `O${i}`, case_id: 'C1', dimension })),
    hard_gate_tasks: ['segment_discovery', 'counter_search_complete', 'strong_candidate_evidence'].map((gate, i) => ({ item_id: `H${i}`, case_id: 'C1', gate })),
    state_tasks: [{ item_id: 'S1', case_id: 'C1', ordinal_item_ids: dimensions.map((_, i) => `O${i}`), hard_gate_item_ids: ['H0', 'H1', 'H2'] }],
  };
  const pack = { cases: [{ case_id: 'C1', evidence_ids: ['E1'], state_context: { demand_direct_evidence: true, supply_direct_evidence: true, counter_evidence_search_complete: true, mandatory_conflict: false, independent_supply_constraint_count: 2, key_source_quality_high: true } }] };
  const result = {
    task_id: 'synthetic', contract_id: 'synthetic', evidence_pack_id: 'pack',
    annotator_id: 'test-model', annotator_role: 'blind_model_annotator', independent_work_attestation: true,
    ordinal: dimensions.map((_, i) => ({ item_id: `O${i}`, evidence_state: 'supported', bound_type: 'lower_bound', rating_min: 2, rating_max: 4, bound_basis: { floor_anchor: 2, ceiling_anchor: null, exact_basis: null, unresolved_higher_anchors: [3, 4], excluded_higher_anchors: [] }, bound_derivation: 'rule_derived', primary_evidence_ids: ['E1'], rationale: 'The source supports a floor; no upper bound is established.' })),
    hard_gate: [0, 1, 2].map(i => ({ item_id: `H${i}`, label: 'pass', evidence_ids: ['E1'], rationale: 'Derived from scoped facts and completed search context.' })),
    state: [{ item_id: 'S1', primary_state: 'watch_segment', derived_metrics: { score_min: 50, score_max: 100, presence_coverage: 1, resolved_coverage: 1, decision_coverage: 0.9 }, achieved_hard_gates: ['H0', 'H1', 'H2'], withheld_reason: null, rationale: 'Evidence supports watch status but not candidate thresholds.' }],
  };
  return { task, pack, result };
}

test('code recomputes coverage without changing model ordinal evidence or raw output', () => {
  const inputs = fixture();
  const before = structuredClone(inputs);
  const computed = materializeCalibration(inputs);
  assert.equal(computed.state[0].derived_metrics.decision_coverage, 0.5);
  assert.deepEqual(computed.ordinal, before.result.ordinal);
  assert.deepEqual(computed.hard_gate, before.result.hard_gate);
  const { derived_metrics: computedMetrics, ...computedState } = computed.state[0];
  const { derived_metrics: rawMetrics, ...rawState } = before.result.state[0];
  assert.deepEqual(computedState, rawState);
  assert.deepEqual(inputs, before);
});

test('mechanical recomputation refuses unsupported evidence IDs', () => {
  const inputs = fixture();
  inputs.result.ordinal[0].primary_evidence_ids = ['OUTSIDE-CASE'];
  assert.throws(() => materializeCalibration(inputs), /evidence_ids/);
});

test('fact path derives gates and states from ordinal evidence without model decisions', () => {
  const inputs = fixture();
  delete inputs.result.hard_gate;
  delete inputs.result.state;
  const computed = completeCalibrationFromOrdinals(inputs);
  assert.equal(computed.state?.[0]?.primary_state, 'watch_segment');
  assert.equal(computed.state[0].derived_metrics.decision_coverage, 0.5);
  assert.deepEqual(computed.hard_gate.map(item => item.label), ['pass', 'pass', 'pass']);
  assert.deepEqual(computed.ordinal, inputs.result.ordinal);
  assert.equal(inputs.result.state, undefined);
});

test('live unknown gaps stay empty while calibration still requires citations', () => {
  const inputs = fixture();
  inputs.pack.cases[0].evidence_ids = [];
  inputs.pack.cases[0].state_context = {};
  for (const item of inputs.result.ordinal) {
    Object.assign(item, { evidence_state: 'unknown', rating_min: 0, rating_max: 4,
      bound_type: 'none', bound_basis: { floor_anchor: null, ceiling_anchor: null, exact_basis: null },
      bound_derivation: 'unresolved', primary_evidence_ids: [] });
  }
  assert.throws(() => completeCalibrationFromOrdinals(inputs), /evidence_ids/);
  const result = completeCalibrationFromOrdinals({ ...inputs, runtimeEvidenceGaps: true });
  assert.equal(result.state[0].primary_state, null);
  assert.equal(result.state[0].derived_metrics.score_max, 100);
  assert.deepEqual(result.ordinal[0].primary_evidence_ids, []);
});

test('runtime gap option never permits resolved scores without source binding', () => {
  const inputs = fixture();
  inputs.result.ordinal[0].primary_evidence_ids = [];
  assert.throws(() => completeCalibrationFromOrdinals({ ...inputs, runtimeEvidenceGaps: true }), /evidence_ids/);
  inputs.result.ordinal[0].primary_evidence_ids = ['OUTSIDE-CASE'];
  assert.throws(() => completeCalibrationFromOrdinals({ ...inputs, runtimeEvidenceGaps: true }), /evidence_ids/);
});
