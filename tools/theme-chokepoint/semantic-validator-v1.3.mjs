const EPSILON = 1e-9;

function roundScore(value) {
  return Math.round((value + Number.EPSILON) * 10) / 10;
}

function roundCoverage(value) {
  return Math.round((value + Number.EPSILON) * 1_000_000) / 1_000_000;
}

function sameSet(left = [], right = []) {
  if (left.length !== right.length) return false;
  const a = [...left].sort();
  const b = [...right].sort();
  return a.every((value, index) => value === b[index]);
}

function familyForStateTask(task, stateTask) {
  return task.semantic_contract?.score_families?.[stateTask.state_family];
}

function ordinalsForStateTask(task, stateTask) {
  const byId = new Map((task.ordinal_tasks ?? []).map((item) => [item.item_id, item]));
  return (stateTask.ordinal_item_ids ?? []).map((id) => byId.get(id)).filter(Boolean);
}

function validatePredicate(predicate, dimensions, prefix, errors) {
  if (!predicate || typeof predicate !== "object") {
    errors.push(`${prefix}:missing_predicate`);
    return;
  }
  if (["ordinal_resolved", "ordinal_min"].includes(predicate.op)) {
    if (!dimensions.has(predicate.dimension)) errors.push(`${prefix}:predicate_unknown_dimension:${predicate.dimension}`);
    if (predicate.op === "ordinal_min" && (!Number.isFinite(predicate.min) || predicate.min < 0 || predicate.min > 4)) {
      errors.push(`${prefix}:predicate_invalid_min`);
    }
    return;
  }
  if (["and", "or"].includes(predicate.op)) {
    if (!Array.isArray(predicate.args) || predicate.args.length === 0) {
      errors.push(`${prefix}:predicate_empty_${predicate.op}`);
      return;
    }
    predicate.args.forEach((child, index) => validatePredicate(child, dimensions, `${prefix}:${index}`, errors));
    return;
  }
  errors.push(`${prefix}:predicate_unknown_op:${predicate.op}`);
}

function validateStateExpression(expression, dimensions, gateNames, stateId, errors) {
  if (!expression || typeof expression !== "object") {
    errors.push(`${stateId}:state_expression_missing`);
    return;
  }
  if (expression.op === "constant") {
    if (typeof expression.value !== "boolean") errors.push(`${stateId}:state_expression_invalid_constant`);
    return;
  }
  if (["metric_gte", "metric_lt"].includes(expression.op)) {
    if (!["score_min", "score_max", "presence_coverage", "resolved_coverage", "decision_coverage"].includes(expression.metric)) {
      errors.push(`${stateId}:state_expression_unknown_metric:${expression.metric}`);
    }
    if (!Number.isFinite(expression.value)) errors.push(`${stateId}:state_expression_invalid_value`);
    return;
  }
  if (["ordinal_resolved", "ordinal_min", "ordinal_max_lt"].includes(expression.op)) {
    if (!dimensions.has(expression.dimension)) errors.push(`${stateId}:state_expression_unknown_dimension:${expression.dimension}`);
    return;
  }
  if (expression.op === "gate_is") {
    if (!gateNames.has(expression.gate)) errors.push(`${stateId}:state_expression_unknown_gate:${expression.gate}`);
    if (!["pass", "fail", "unknown"].includes(expression.label)) errors.push(`${stateId}:state_expression_invalid_gate_label:${expression.label}`);
    return;
  }
  if (["and", "or"].includes(expression.op)) {
    if (!Array.isArray(expression.args) || expression.args.length === 0) {
      errors.push(`${stateId}:state_expression_empty_${expression.op}`);
      return;
    }
    expression.args.forEach((child) => validateStateExpression(child, dimensions, gateNames, stateId, errors));
    return;
  }
  if (expression.op === "not") {
    validateStateExpression(expression.arg, dimensions, gateNames, stateId, errors);
    return;
  }
  errors.push(`${stateId}:state_expression_unknown_op:${expression.op}`);
}

function validateCanonicalBinding(task, canonicalContract, errors) {
  if (!canonicalContract) return;
  for (const [familyName, family] of Object.entries(canonicalContract.score_families ?? {})) {
    const weightSum = Object.values(family.weighted_dimensions ?? {}).reduce((sum, weight) => sum + weight, 0);
    if (weightSum !== 100) {
      errors.push(`semantic_contract:canonical_weight_sum:${familyName}:expected_100:actual_${weightSum}`);
    }
  }
  if (task.semantic_contract?.version !== canonicalContract.contract_id) {
    errors.push(`semantic_contract:version_mismatch:expected_${canonicalContract.contract_id}:actual_${task.semantic_contract?.version ?? "missing"}`);
  }
  for (const [familyName, family] of Object.entries(task.semantic_contract?.score_families ?? {})) {
    const canonicalFamily = canonicalContract.score_families?.[familyName];
    if (!canonicalFamily) {
      errors.push(`semantic_contract:unknown_family:${familyName}`);
      continue;
    }
    const expectedWeights = canonicalFamily.weighted_dimensions ?? {};
    const expectedRequirements = canonicalFamily.supported_evidence_requirements ?? {};
    for (const [dimension, expectedWeight] of Object.entries(expectedWeights)) {
      const actual = family.dimensions?.[dimension];
      if (!actual) {
        errors.push(`semantic_contract:missing_dimension:${familyName}:${dimension}`);
        continue;
      }
      if (actual.weight !== expectedWeight) {
        errors.push(`semantic_contract:weight_mismatch:${familyName}:${dimension}:expected_${expectedWeight}:actual_${actual.weight}`);
      }
      const expectedCapabilities = expectedRequirements[dimension] ?? [];
      const actualCapabilities = actual.supported_evidence_any_of ?? [];
      if (!sameSet(actualCapabilities, expectedCapabilities)) {
        errors.push(`semantic_contract:evidence_requirement_mismatch:${familyName}:${dimension}`);
      }
    }
    for (const dimension of Object.keys(family.dimensions ?? {})) {
      if (!Object.hasOwn(expectedWeights, dimension) && family.dimensions[dimension].weight > 0) {
        errors.push(`semantic_contract:unexpected_weighted_dimension:${familyName}:${dimension}`);
      }
    }
  }
}

export function validateTaskContract(task, canonicalContract = null) {
  const errors = [];
  const ordinalById = new Map((task.ordinal_tasks ?? []).map((item) => [item.item_id, item]));
  const gateById = new Map((task.hard_gate_tasks ?? []).map((item) => [item.item_id, item]));

  if (!task.semantic_contract?.version) errors.push("semantic_contract:missing_version");
  validateCanonicalBinding(task, canonicalContract, errors);

  for (const stateTask of task.state_tasks ?? []) {
    const family = familyForStateTask(task, stateTask);
    if (!family) {
      errors.push(`${stateTask.item_id}:unknown_state_family:${stateTask.state_family}`);
      continue;
    }
    if (!family.state_policy?.type) errors.push(`${stateTask.item_id}:missing_state_policy`);
    else if (!["earnings_v1.3", "ordered_rules_v1.3"].includes(family.state_policy.type)) {
      errors.push(`${stateTask.item_id}:unsupported_state_policy:${family.state_policy.type}`);
    }
    if (!Array.isArray(stateTask.ordinal_item_ids)) errors.push(`${stateTask.item_id}:ordinal_item_ids_required`);
    if (!Array.isArray(stateTask.hard_gate_item_ids)) errors.push(`${stateTask.item_id}:hard_gate_item_ids_required`);

    const mappedOrdinals = [];
    for (const id of stateTask.ordinal_item_ids ?? []) {
      const item = ordinalById.get(id);
      if (!item) {
        errors.push(`${stateTask.item_id}:unknown_ordinal_item:${id}`);
        continue;
      }
      if (item.case_id !== stateTask.case_id) errors.push(`${stateTask.item_id}:cross_case_ordinal:${id}`);
      if (item.score_family !== stateTask.state_family) errors.push(`${stateTask.item_id}:cross_family_ordinal:${id}`);
      mappedOrdinals.push(item);
    }

    const dimensionCounts = new Map();
    for (const item of mappedOrdinals) dimensionCounts.set(item.dimension, (dimensionCounts.get(item.dimension) ?? 0) + 1);
    for (const [dimension, definition] of Object.entries(family.dimensions ?? {})) {
      if (!(definition.weight > 0)) continue;
      const count = dimensionCounts.get(dimension) ?? 0;
      if (count === 0) errors.push(`${stateTask.item_id}:missing_weighted_dimension:${dimension}`);
      if (count > 1) errors.push(`${stateTask.item_id}:duplicate_weighted_dimension:${dimension}`);
    }
    for (const item of mappedOrdinals) {
      if (!Object.hasOwn(family.dimensions ?? {}, item.dimension)) {
        errors.push(`${stateTask.item_id}:dimension_not_in_family:${item.dimension}`);
      }
    }

    const dimensions = new Set(Object.keys(family.dimensions ?? {}));
    const gateNames = new Set();
    for (const id of stateTask.hard_gate_item_ids ?? []) {
      const gate = gateById.get(id);
      if (!gate) {
        errors.push(`${stateTask.item_id}:unknown_hard_gate_item:${id}`);
        continue;
      }
      if (gate.case_id !== stateTask.case_id) errors.push(`${stateTask.item_id}:cross_case_hard_gate:${id}`);
      gateNames.add(gate.gate);
      validatePredicate(gate.predicate, dimensions, id, errors);
    }
    if (family.state_policy?.type === "earnings_v1.3") {
      for (const dimension of family.state_policy.mandatory_dimensions ?? []) {
        if (!dimensions.has(dimension)) errors.push(`${stateTask.item_id}:state_policy_unknown_dimension:${dimension}`);
      }
    }
    if (family.state_policy?.type === "ordered_rules_v1.3") {
      validateStateExpression(family.state_policy.eligibility, dimensions, gateNames, stateTask.item_id, errors);
      if (!Array.isArray(family.state_policy.rules) || family.state_policy.rules.length === 0) {
        errors.push(`${stateTask.item_id}:state_policy_rules_required`);
      } else {
        for (const rule of family.state_policy.rules) {
          if (!rule.state) errors.push(`${stateTask.item_id}:state_policy_rule_state_required`);
          validateStateExpression(rule.when, dimensions, gateNames, stateTask.item_id, errors);
        }
      }
    }
  }
  return errors;
}

function evaluateOrdinalPredicate(predicate, ordinalByDimension) {
  if (predicate.op === "ordinal_resolved") {
    const ordinal = ordinalByDimension.get(predicate.dimension);
    return ordinal?.evidence_state === "supported" ? "pass" : "unknown";
  }
  if (predicate.op === "ordinal_min") {
    const ordinal = ordinalByDimension.get(predicate.dimension);
    if (!ordinal || ordinal.evidence_state !== "supported") return "unknown";
    if (ordinal.rating_min >= predicate.min) return "pass";
    if (ordinal.rating_max < predicate.min) return "fail";
    return "unknown";
  }
  if (["and", "or"].includes(predicate.op)) {
    const values = predicate.args.map((child) => evaluateOrdinalPredicate(child, ordinalByDimension));
    if (predicate.op === "and") {
      if (values.includes("fail")) return "fail";
      if (values.includes("unknown")) return "unknown";
      return "pass";
    }
    if (values.includes("pass")) return "pass";
    if (values.includes("unknown")) return "unknown";
    return "fail";
  }
  return "unknown";
}

function deriveMetrics(family, ordinalByDimension) {
  const entries = Object.entries(family.dimensions ?? {}).filter(([, definition]) => definition.weight > 0);
  const totalWeight = entries.reduce((sum, [, definition]) => sum + definition.weight, 0);
  let scoreMin = 0;
  let scoreMax = 0;
  let presence = 0;
  let resolved = 0;
  let decision = 0;

  for (const [dimension, definition] of entries) {
    const ordinal = ordinalByDimension.get(dimension);
    const ratingMin = ordinal?.rating_min ?? 0;
    const ratingMax = ordinal?.rating_max ?? 4;
    const state = ordinal?.evidence_state ?? "unknown";
    const presenceCredit = ["supported", "conflicted"].includes(state) ? 1 : 0;
    const resolvedCredit = state === "supported" ? 1 : 0;
    const resolutionCredit = 1 - (ratingMax - ratingMin) / 4;
    scoreMin += definition.weight * ratingMin / 4;
    scoreMax += definition.weight * ratingMax / 4;
    presence += definition.weight * presenceCredit;
    resolved += definition.weight * resolvedCredit;
    decision += definition.weight * resolvedCredit * resolutionCredit;
  }

  return {
    score_min: roundScore(scoreMin),
    score_max: roundScore(scoreMax),
    presence_coverage: roundCoverage(presence / totalWeight),
    resolved_coverage: roundCoverage(resolved / totalWeight),
    decision_coverage: roundCoverage(decision / totalWeight),
  };
}

function deriveEarningsState(policy, metrics, ordinalByDimension) {
  const mandatory = policy.mandatory_dimensions.map((dimension) => ordinalByDimension.get(dimension));
  const mandatoryResolved = mandatory.every((item) => item?.evidence_state === "supported");
  const eligible = metrics.decision_coverage + EPSILON >= policy.eligibility_decision_coverage_min && mandatoryResolved;
  const hardFail = mandatory.some((item) => item?.evidence_state === "supported" && item.rating_max < policy.hard_fail_rating_max_below);
  const weak = hardFail || (
    metrics.decision_coverage + EPSILON >= policy.eligibility_decision_coverage_min
    && metrics.score_max < policy.weak_score_max_below
  );
  const material = eligible
    && !hardFail
    && metrics.score_min + EPSILON >= policy.material_score_min
    && metrics.decision_coverage + EPSILON >= policy.material_decision_coverage_min
    && mandatory.every((item) => item.rating_min >= policy.material_mandatory_rating_min);

  if (weak) return policy.states.weak;
  if (!eligible) return null;
  if (material) return policy.states.material;
  return policy.states.moderate;
}

function evaluateStateExpression(expression, metrics, ordinalByDimension, gateLabelsByName) {
  if (!expression || typeof expression !== "object") return false;
  if (expression.op === "constant") return expression.value === true;
  if (expression.op === "metric_gte") return Number.isFinite(metrics[expression.metric]) && metrics[expression.metric] + EPSILON >= expression.value;
  if (expression.op === "metric_lt") return Number.isFinite(metrics[expression.metric]) && metrics[expression.metric] < expression.value;
  if (expression.op === "ordinal_resolved") return ordinalByDimension.get(expression.dimension)?.evidence_state === "supported";
  if (expression.op === "ordinal_min") {
    const ordinal = ordinalByDimension.get(expression.dimension);
    return ordinal?.evidence_state === "supported" && ordinal.rating_min >= expression.min;
  }
  if (expression.op === "ordinal_max_lt") {
    const ordinal = ordinalByDimension.get(expression.dimension);
    return ordinal?.evidence_state === "supported" && ordinal.rating_max < expression.value;
  }
  if (expression.op === "gate_is") return gateLabelsByName.get(expression.gate) === expression.label;
  if (expression.op === "and") return Array.isArray(expression.args) && expression.args.every((child) => evaluateStateExpression(child, metrics, ordinalByDimension, gateLabelsByName));
  if (expression.op === "or") return Array.isArray(expression.args) && expression.args.some((child) => evaluateStateExpression(child, metrics, ordinalByDimension, gateLabelsByName));
  if (expression.op === "not") return !evaluateStateExpression(expression.arg, metrics, ordinalByDimension, gateLabelsByName);
  return false;
}

function deriveOrderedState(policy, metrics, ordinalByDimension, gateLabelsByName) {
  if (!evaluateStateExpression(policy.eligibility, metrics, ordinalByDimension, gateLabelsByName)) return null;
  for (const rule of policy.rules ?? []) {
    if (evaluateStateExpression(rule.when, metrics, ordinalByDimension, gateLabelsByName)) return rule.state;
  }
  return null;
}

function deriveState(family, metrics, ordinalByDimension, gateLabelsByName) {
  if (family.state_policy.type === "earnings_v1.3") {
    return deriveEarningsState(family.state_policy, metrics, ordinalByDimension);
  }
  if (family.state_policy.type === "ordered_rules_v1.3") {
    return deriveOrderedState(family.state_policy, metrics, ordinalByDimension, gateLabelsByName);
  }
  return null;
}

function capabilitiesForEvidence(pack) {
  return new Map((pack.evidence ?? []).map((item) => [item.evidence_id, new Set(item.claim_capabilities ?? [])]));
}

function validateEvidenceCapabilities(task, pack, result, errors) {
  const ordinalTaskById = new Map((task.ordinal_tasks ?? []).map((item) => [item.item_id, item]));
  const capabilities = capabilitiesForEvidence(pack);
  for (const ordinal of result.ordinal ?? []) {
    if (ordinal.evidence_state !== "supported") continue;
    const taskItem = ordinalTaskById.get(ordinal.item_id);
    if (!taskItem) continue;
    const family = task.semantic_contract.score_families[taskItem.score_family];
    const required = family?.dimensions?.[taskItem.dimension]?.supported_evidence_any_of ?? [];
    if (required.length === 0) continue;
    const cited = new Set();
    for (const evidenceId of ordinal.primary_evidence_ids ?? []) {
      for (const capability of capabilities.get(evidenceId) ?? []) cited.add(capability);
    }
    if (!required.some((capability) => cited.has(capability))) {
      errors.push(`${ordinal.item_id}:missing_supported_evidence_capability:${required.join("|")}`);
    }
  }
}

export function validateSubmissionSemantics({ task, pack, result, canonicalContract = null }) {
  const errors = [...validateTaskContract(task, canonicalContract)];
  validateEvidenceCapabilities(task, pack, result, errors);

  const ordinalResultById = new Map((result.ordinal ?? []).map((item) => [item.item_id, item]));
  const gateResultById = new Map((result.hard_gate ?? []).map((item) => [item.item_id, item]));
  const stateResultById = new Map((result.state ?? []).map((item) => [item.item_id, item]));
  const ordinalTaskById = new Map((task.ordinal_tasks ?? []).map((item) => [item.item_id, item]));
  const gateTaskById = new Map((task.hard_gate_tasks ?? []).map((item) => [item.item_id, item]));
  const derived = {};

  for (const stateTask of task.state_tasks ?? []) {
    const family = familyForStateTask(task, stateTask);
    if (!family) continue;
    const ordinalByDimension = new Map();
    for (const itemId of stateTask.ordinal_item_ids ?? []) {
      const taskItem = ordinalTaskById.get(itemId);
      const resultItem = ordinalResultById.get(itemId);
      if (taskItem && resultItem) ordinalByDimension.set(taskItem.dimension, resultItem);
    }

    const expectedGateLabels = new Map();
    const gateLabelsByName = new Map();
    for (const gateId of stateTask.hard_gate_item_ids ?? []) {
      const gateTask = gateTaskById.get(gateId);
      if (!gateTask) continue;
      const expected = evaluateOrdinalPredicate(gateTask.predicate, ordinalByDimension);
      expectedGateLabels.set(gateId, expected);
      gateLabelsByName.set(gateTask.gate, expected);
      const actual = gateResultById.get(gateId)?.label;
      if (actual !== expected) errors.push(`${gateId}:semantic_label_mismatch:expected_${expected}:actual_${actual ?? "missing"}`);
    }

    const metrics = deriveMetrics(family, ordinalByDimension);
    const expectedState = deriveState(family, metrics, ordinalByDimension, gateLabelsByName);
    const actualState = stateResultById.get(stateTask.item_id);
    const actualName = actualState?.primary_state ?? null;
    if (actualName !== expectedState) {
      errors.push(`${stateTask.item_id}:semantic_state_mismatch:expected_${expectedState ?? "withheld"}:actual_${actualName ?? "withheld"}`);
    }
    for (const [metric, expected] of Object.entries(metrics)) {
      const actual = actualState?.derived_metrics?.[metric];
      if (!Number.isFinite(actual) || Math.abs(actual - expected) > EPSILON) {
        errors.push(`${stateTask.item_id}:derived_metric_mismatch:${metric}:expected_${expected}:actual_${actual ?? "missing"}`);
      }
    }
    const expectedPassedGates = [...expectedGateLabels.entries()].filter(([, label]) => label === "pass").map(([id]) => id);
    if (!sameSet(actualState?.achieved_hard_gates, expectedPassedGates)) {
      errors.push(`${stateTask.item_id}:achieved_hard_gates_mismatch`);
    }
    if (expectedState === null && !actualState?.withheld_reason) errors.push(`${stateTask.item_id}:withheld_reason_required`);
    if (expectedState !== null && actualState?.withheld_reason) errors.push(`${stateTask.item_id}:unexpected_withheld_reason`);

    derived[stateTask.item_id] = {
      metrics,
      hard_gate_labels: Object.fromEntries(expectedGateLabels),
      primary_state: expectedState,
    };
  }

  return { valid: errors.length === 0, errors, derived };
}
