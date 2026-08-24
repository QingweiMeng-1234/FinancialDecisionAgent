const DIMS = [
  "demand_pressure",
  "downstream_criticality",
  "effective_supply_concentration",
  "qualification_barrier",
  "capacity_inelasticity",
  "substitute_weakness",
];

const EPSILON = 1e-9;
const round = (value, digits = 4) => Number(value.toFixed(digits));
const sameSet = (a, b) => Array.isArray(a) && a.length === new Set(a).size
  && a.length === b.length && a.every((item) => b.includes(item));

function expectedIds(actual, expected, label, errors) {
  if (!Array.isArray(actual)) {
    errors.push(`${label}:not_array`);
    return;
  }
  const ids = actual.map((item) => item?.item_id);
  if (ids.length !== new Set(ids).size) errors.push(`${label}:duplicate_ids`);
  for (const id of expected.map((item) => item.item_id)) if (!ids.includes(id)) errors.push(`${label}:missing:${id}`);
  for (const id of ids) if (!expected.some((item) => item.item_id === id)) errors.push(`${label}:unexpected:${id}`);
}

function validateBound(item, errors) {
  const id = item.item_id ?? "ordinal";
  if (!Number.isInteger(item.rating_min) || !Number.isInteger(item.rating_max)
    || item.rating_min < 0 || item.rating_max > 4 || item.rating_min > item.rating_max) {
    errors.push(`${id}:rating_range`);
    return;
  }
  const basis = item.bound_basis ?? {};
  if (item.evidence_state === "unknown") {
    if (item.bound_type !== "none" || item.rating_min !== 0 || item.rating_max !== 4) errors.push(`${id}:unknown_must_be_0_4_none`);
    if (basis.floor_anchor !== null || basis.ceiling_anchor !== null || basis.exact_basis !== null) errors.push(`${id}:unknown_bound_basis`);
    return;
  }
  if (item.bound_type === "exact") {
    if (item.rating_min !== item.rating_max) errors.push(`${id}:exact_range`);
    if (basis.floor_anchor !== item.rating_min || basis.ceiling_anchor !== item.rating_max) errors.push(`${id}:exact_anchors`);
    const allowed = item.rating_min === 4 ? ["natural_cap"] : ["direct_upper_bound", "contract_exclusivity"];
    if (!allowed.includes(basis.exact_basis)) errors.push(`${id}:exact_basis`);
    const expectedExcluded = Array.from({ length: 4 - item.rating_max }, (_, i) => item.rating_max + i + 1);
    if (item.rating_max < 4 && !sameSet(basis.excluded_higher_anchors, expectedExcluded)) errors.push(`${id}:excluded_higher_anchors`);
  } else if (item.bound_type === "lower_bound") {
    if (item.rating_min <= 0 || item.rating_max !== 4 || basis.floor_anchor !== item.rating_min
      || basis.ceiling_anchor !== null || basis.exact_basis !== null) errors.push(`${id}:lower_bound_basis`);
  } else if (item.bound_type === "upper_bound") {
    if (item.rating_min !== 0 || item.rating_max >= 4 || basis.floor_anchor !== null
      || basis.ceiling_anchor !== item.rating_max || basis.exact_basis !== null) errors.push(`${id}:upper_bound_basis`);
  } else if (item.bound_type === "interval") {
    if (item.rating_min === item.rating_max || basis.floor_anchor !== item.rating_min
      || basis.ceiling_anchor !== item.rating_max || basis.exact_basis !== null) errors.push(`${id}:interval_basis`);
  } else {
    errors.push(`${id}:bound_type`);
  }
}

function deriveMetrics(weights, ordinalByDimension) {
  let scoreMin = 0; let scoreMax = 0; let presence = 0; let resolved = 0; let decision = 0;
  for (const dimension of DIMS) {
    const weight = weights[dimension];
    const item = ordinalByDimension.get(dimension);
    const min = item?.rating_min ?? 0; const max = item?.rating_max ?? 4;
    const supported = item?.evidence_state === "supported";
    const present = supported || item?.evidence_state === "conflicted";
    scoreMin += weight * min / 4; scoreMax += weight * max / 4;
    if (present) presence += weight;
    if (supported) {
      resolved += weight;
      decision += weight * (1 - (max - min) / 4);
    }
  }
  return {
    score_min: round(scoreMin, 1), score_max: round(scoreMax, 1),
    presence_coverage: round(presence / 100), resolved_coverage: round(resolved / 100),
    decision_coverage: round(decision / 100),
  };
}

function discoveryLabel(ord, context) {
  const demand = ord.get("demand_pressure"); const critical = ord.get("downstream_criticality");
  if (!demand || !critical) return "unknown";
  if (demand.rating_max < 1 || critical.rating_max < 1) return "fail";
  const supply = ["effective_supply_concentration", "qualification_barrier", "capacity_inelasticity", "substitute_weakness"]
    .some((dimension) => (ord.get(dimension)?.rating_min ?? 0) >= 2);
  if (demand.rating_min >= 1 && critical.rating_min >= 1 && supply
    && context.demand_direct_evidence === true && context.supply_direct_evidence === true) return "pass";
  return "unknown";
}

function counterSearchLabel(context) {
  if (context.counter_evidence_search_complete === true) return "pass";
  if (context.counter_evidence_search_complete === false) return "fail";
  return "unknown";
}

function strongEvidenceLabel(context) {
  const fieldsKnown = ["mandatory_conflict", "key_source_quality_high", "independent_supply_constraint_count"]
    .every((key) => context[key] !== null && context[key] !== undefined);
  if (!fieldsKnown) return "unknown";
  return context.mandatory_conflict === false && context.key_source_quality_high === true
    && context.independent_supply_constraint_count >= 2 ? "pass" : "fail";
}

function deriveState(metrics, ord, context) {
  const demand = ord.get("demand_pressure"); const critical = ord.get("downstream_criticality");
  const supplyPositive = ["effective_supply_concentration", "qualification_barrier", "capacity_inelasticity", "substitute_weakness"]
    .some((dimension) => (ord.get(dimension)?.rating_min ?? 0) >= 2);
  const eligible = (demand?.rating_max < 1) || (critical?.rating_max < 1)
    || ((demand?.rating_min ?? 0) >= 1 && (critical?.rating_min ?? 0) >= 1 && supplyPositive
      && context.demand_direct_evidence === true && context.supply_direct_evidence === true)
    || (metrics.decision_coverage + EPSILON >= 0.65
      && (metrics.score_max < 60 || demand?.rating_max < 1 || critical?.rating_max < 1));
  if (!eligible) return null;
  if (demand?.rating_max < 1 || critical?.rating_max < 1
    || (metrics.decision_coverage + EPSILON >= 0.6 && metrics.score_max < 40)) return "not_supported";
  if (metrics.score_min + EPSILON >= 80 && metrics.decision_coverage + EPSILON >= 0.8
    && demand.rating_min >= 3 && critical.rating_min >= 3
    && context.demand_direct_evidence === true && context.supply_direct_evidence === true
    && context.counter_evidence_search_complete === true && context.mandatory_conflict === false
    && context.independent_supply_constraint_count >= 2 && context.key_source_quality_high === true) return "strong_candidate_chokepoint";
  if (metrics.score_min + EPSILON >= 65 && metrics.decision_coverage + EPSILON >= 0.65
    && demand.rating_min >= 2 && critical.rating_min >= 2
    && context.demand_direct_evidence === true && context.supply_direct_evidence === true
    && context.counter_evidence_search_complete === true && context.mandatory_conflict === false) return "candidate_chokepoint";
  if (metrics.presence_coverage + EPSILON >= 0.5 && metrics.decision_coverage + EPSILON >= 0.2
    && metrics.score_max + EPSILON >= 60) return "watch_segment";
  return "not_supported";
}

export function validateSubmission({ task, pack, result }) {
  const errors = [];
  if (result.task_id !== task.task_id) errors.push("task_id_mismatch");
  if (result.contract_id !== task.contract_id) errors.push("contract_id_mismatch");
  if (result.evidence_pack_id !== task.evidence_pack_id) errors.push("evidence_pack_id_mismatch");
  if (!result.annotator_id || typeof result.annotator_id !== "string") errors.push("annotator_id_required");
  if (!task.allowed_annotator_roles.includes(result.annotator_role)) errors.push("annotator_role_invalid");
  if (result.independent_work_attestation !== true) errors.push("independent_work_attestation_required");
  expectedIds(result.ordinal, task.ordinal_tasks, "ordinal", errors);
  expectedIds(result.hard_gate, task.hard_gate_tasks, "hard_gate", errors);
  expectedIds(result.state, task.state_tasks, "state", errors);
  const evidenceByCase = new Map(pack.cases.map((item) => [item.case_id, new Set(item.evidence_ids)]));
  const ordinalTask = new Map(task.ordinal_tasks.map((item) => [item.item_id, item]));
  for (const item of Array.isArray(result.ordinal) ? result.ordinal : []) {
    const def = ordinalTask.get(item.item_id); if (!def) continue;
    if (!["supported", "conflicted", "unknown"].includes(item.evidence_state)) errors.push(`${item.item_id}:evidence_state`);
    if (!Array.isArray(item.primary_evidence_ids) || item.primary_evidence_ids.length === 0
      || item.primary_evidence_ids.some((id) => !evidenceByCase.get(def.case_id)?.has(id))) errors.push(`${item.item_id}:evidence_ids`);
    if (typeof item.rationale !== "string" || item.rationale.trim().length < 12) errors.push(`${item.item_id}:rationale`);
    validateBound(item, errors);
  }
  const gateDefinitions = new Map(task.hard_gate_tasks.map((item) => [item.item_id, item]));
  for (const item of Array.isArray(result.hard_gate) ? result.hard_gate : []) {
    const def = gateDefinitions.get(item.item_id); if (!def) continue;
    if (!["pass", "fail", "unknown"].includes(item.label)) errors.push(`${item.item_id}:label`);
    if (!Array.isArray(item.evidence_ids) || item.evidence_ids.length === 0
      || item.evidence_ids.some((id) => !evidenceByCase.get(def.case_id)?.has(id))) errors.push(`${item.item_id}:evidence_ids`);
    if (typeof item.rationale !== "string" || item.rationale.trim().length < 12) errors.push(`${item.item_id}:rationale`);
  }
  for (const item of Array.isArray(result.state) ? result.state : []) {
    if (typeof item.rationale !== "string" || item.rationale.trim().length < 12) errors.push(`${item.item_id}:rationale`);
    if (!Array.isArray(item.achieved_hard_gates)) errors.push(`${item.item_id}:achieved_hard_gates`);
  }
  const ordinalResult = new Map((result.ordinal ?? []).map((item) => [item.item_id, item]));
  const gateResult = new Map((result.hard_gate ?? []).map((item) => [item.item_id, item]));
  const stateResult = new Map((result.state ?? []).map((item) => [item.item_id, item]));
  const gateTask = new Map(task.hard_gate_tasks.map((item) => [item.item_id, item]));
  const derived = {};
  for (const stateTask of task.state_tasks) {
    const ord = new Map(stateTask.ordinal_item_ids.map((id) => [ordinalTask.get(id).dimension, ordinalResult.get(id)]));
    const context = pack.cases.find((item) => item.case_id === stateTask.case_id)?.state_context ?? {};
    const metrics = deriveMetrics(task.segment_weights, ord);
    const expectedGates = new Map();
    for (const id of stateTask.hard_gate_item_ids) {
      const kind = gateTask.get(id)?.gate;
      const label = kind === "segment_discovery" ? discoveryLabel(ord, context)
        : kind === "counter_search_complete" ? counterSearchLabel(context)
          : strongEvidenceLabel(context);
      expectedGates.set(id, label);
      if (gateResult.get(id)?.label !== label) errors.push(`${id}:semantic_label_mismatch:expected_${label}`);
    }
    const expectedState = deriveState(metrics, ord, context);
    const actual = stateResult.get(stateTask.item_id);
    if ((actual?.primary_state ?? null) !== expectedState) errors.push(`${stateTask.item_id}:semantic_state_mismatch:expected_${expectedState ?? "withheld"}`);
    for (const [key, expected] of Object.entries(metrics)) {
      if (!Number.isFinite(actual?.derived_metrics?.[key]) || Math.abs(actual.derived_metrics[key] - expected) > EPSILON) errors.push(`${stateTask.item_id}:derived_metric_mismatch:${key}:expected_${expected}`);
    }
    const passed = [...expectedGates].filter(([, label]) => label === "pass").map(([id]) => id);
    if (!sameSet(actual?.achieved_hard_gates, passed)) errors.push(`${stateTask.item_id}:achieved_hard_gates_mismatch`);
    if (expectedState === null && !actual?.withheld_reason) errors.push(`${stateTask.item_id}:withheld_reason_required`);
    if (expectedState !== null && actual?.withheld_reason) errors.push(`${stateTask.item_id}:unexpected_withheld_reason`);
    derived[stateTask.item_id] = { metrics, hard_gates: Object.fromEntries(expectedGates), primary_state: expectedState };
  }
  return { valid: errors.length === 0, errors, derived };
}
