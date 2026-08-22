import assert from "node:assert/strict";
import test from "node:test";
import { validateSubmission } from "./semantic-validator-v1.5.mjs";

const dimensions = ["demand_pressure", "downstream_criticality", "effective_supply_concentration", "qualification_barrier", "capacity_inelasticity", "substitute_weakness"];
const task = {
  task_id: "test-v1.5", contract_id: "theme-chokepoint-scoring-v1.5", evidence_pack_id: "pack-v1.5",
  allowed_annotator_roles: ["internal_blind_annotator", "external_blind_annotator"],
  segment_weights: { demand_pressure: 15, downstream_criticality: 20, effective_supply_concentration: 15, qualification_barrier: 15, capacity_inelasticity: 15, substitute_weakness: 20 },
  ordinal_tasks: dimensions.map((dimension, index) => ({ item_id: `O${index}`, case_id: "C1", dimension })),
  hard_gate_tasks: [
    { item_id: "H1", case_id: "C1", gate: "segment_discovery" },
    { item_id: "H2", case_id: "C1", gate: "counter_search_complete" },
    { item_id: "H3", case_id: "C1", gate: "strong_candidate_evidence" },
  ],
  state_tasks: [{ item_id: "S1", case_id: "C1", ordinal_item_ids: dimensions.map((_, index) => `O${index}`), hard_gate_item_ids: ["H1", "H2", "H3"] }],
};
const pack = { cases: [{ case_id: "C1", evidence_ids: ["E1"], state_context: { demand_direct_evidence: true, supply_direct_evidence: true, counter_evidence_search_complete: true, mandatory_conflict: false, independent_supply_constraint_count: 0, key_source_quality_high: true } }] };
const exactZero = (item_id) => ({ item_id, evidence_state: "supported", bound_type: "exact", rating_min: 0, rating_max: 0, primary_evidence_ids: ["E1"], bound_basis: { floor_anchor: 0, ceiling_anchor: 0, exact_basis: "direct_upper_bound", unresolved_higher_anchors: [], excluded_higher_anchors: [1, 2, 3, 4] }, rationale: "Direct evidence fixes the complete zero anchor." });
const valid = () => ({
  task_id: task.task_id, contract_id: task.contract_id, evidence_pack_id: task.evidence_pack_id,
  annotator_id: "a123", annotator_role: "internal_blind_annotator", independent_work_attestation: true,
  ordinal: task.ordinal_tasks.map((item) => exactZero(item.item_id)),
  hard_gate: [
    { item_id: "H1", label: "fail", evidence_ids: ["E1"], rationale: "Demand and criticality are fixed below the discovery floor." },
    { item_id: "H2", label: "pass", evidence_ids: ["E1"], rationale: "The controlled context explicitly completes counter-search." },
    { item_id: "H3", label: "fail", evidence_ids: ["E1"], rationale: "The controlled context has fewer than two constraints." },
  ],
  state: [{ item_id: "S1", primary_state: "not_supported", achieved_hard_gates: ["H2"], derived_metrics: { score_min: 0, score_max: 0, presence_coverage: 1, resolved_coverage: 1, decision_coverage: 1 }, withheld_reason: null, rationale: "Resolved demand and criticality are both exact zero." }],
});

test("accepts a mechanically consistent exact-zero Segment submission", () => {
  const report = validateSubmission({ task, pack, result: valid() });
  assert.equal(report.valid, true, report.errors.join("\n"));
});

test("rejects a model-authored Final State that differs from mechanical derivation", () => {
  const result = valid(); result.state[0].primary_state = "strong_candidate_chokepoint";
  const report = validateSubmission({ task, pack, result });
  assert.equal(report.valid, false); assert.ok(report.errors.some((item) => item.includes("semantic_state_mismatch")));
});

test("rejects unknown evidence disguised as an exact score", () => {
  const result = valid(); result.ordinal[0].evidence_state = "unknown";
  const report = validateSubmission({ task, pack, result });
  assert.equal(report.valid, false); assert.ok(report.errors.some((item) => item.includes("unknown_must_be_0_4_none")));
});

