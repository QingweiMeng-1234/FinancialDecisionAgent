import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

const BUSINESS_FACT_DECISION_TABLE = JSON.parse(
  fs.readFileSync(new URL("./business-fact-decision-table-v1.4.json", import.meta.url), "utf8"),
);

const TRUSTED_VERIFICATION_AUTHORITIES = new WeakSet();

function fileSha256(file) {
  return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function sha256Bytes(value) {
  return createHash("sha256").update(value).digest("hex");
}

function normalizeSourceText(value) {
  const namedEntities = {
    amp: "&", apos: "'", gt: ">", lt: "<", nbsp: " ", quot: '"',
  };
  const unescaped = String(value).replace(
    /&(#x[0-9a-f]+|#\d+|[a-z]+);/gi,
    (match, entity) => {
      if (entity.startsWith("#x") || entity.startsWith("#X")) {
        return String.fromCodePoint(Number.parseInt(entity.slice(2), 16));
      }
      if (entity.startsWith("#")) {
        return String.fromCodePoint(Number.parseInt(entity.slice(1), 10));
      }
      return namedEntities[entity.toLowerCase()] ?? match;
    },
  );
  return unescaped.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function loadRuntimeGovernanceAuthority({
  runtimeGovernanceBundlePath,
  expectedRuntimeGovernanceBundleId,
  expectedRuntimeGovernanceBundleSha256,
}) {
  const bundlePath = path.resolve(runtimeGovernanceBundlePath ?? "");
  if (!fs.existsSync(bundlePath)) throw new Error("runtime governance bundle is required");
  const actualBundleSha256 = fileSha256(bundlePath);
  if (!/^[0-9a-f]{64}$/.test(expectedRuntimeGovernanceBundleSha256 ?? "")
    || actualBundleSha256 !== expectedRuntimeGovernanceBundleSha256) {
    throw new Error("runtime governance bundle SHA-256 mismatch");
  }
  const bundle = JSON.parse(fs.readFileSync(bundlePath, "utf8"));
  if (!expectedRuntimeGovernanceBundleId
    || bundle.bundle_id !== expectedRuntimeGovernanceBundleId
    || bundle.schema_version !== "theme-chokepoint-runtime-governance-bundle-v1") {
    throw new Error("runtime governance bundle identity mismatch");
  }
  const base = path.dirname(bundlePath);
  const resolved = {};
  for (const name of [
    "runtime_overlay",
    "business_fact_decision_table",
    "source_identity_schema",
    "source_identity_policy",
  ]) {
    const binding = bundle.artifacts?.[name];
    if (!binding?.path || !/^[0-9a-f]{64}$/.test(binding.sha256 ?? "")) {
      throw new Error(`runtime governance artifact binding is invalid: ${name}`);
    }
    const artifactPath = path.resolve(base, binding.path);
    const relative = path.relative(base, artifactPath);
    if (relative.startsWith("..") || path.isAbsolute(relative)
      || !fs.existsSync(artifactPath) || fileSha256(artifactPath) !== binding.sha256) {
      throw new Error(`runtime governance artifact SHA-256 mismatch: ${name}`);
    }
    resolved[name] = artifactPath;
  }
  return {
    bundleId: bundle.bundle_id,
    bundleSha256: actualBundleSha256,
    decisionTable: JSON.parse(
      fs.readFileSync(resolved.business_fact_decision_table, "utf8"),
    ),
    sourceIdentityPolicy: JSON.parse(
      fs.readFileSync(resolved.source_identity_policy, "utf8"),
    ),
    sourceIdentityPolicySha256: bundle.artifacts.source_identity_policy.sha256,
  };
}

function validateRepositorySourceLineage(row, governance) {
  let sourceContext;
  let redirectChain;
  let quoteSpan;
  try {
    sourceContext = JSON.parse(row.source_context_json);
    redirectChain = JSON.parse(row.redirect_chain_json);
    quoteSpan = JSON.parse(row.quote_span_json);
  } catch {
    throw new Error("verification repository source context is invalid");
  }
  if (!sourceContext || Array.isArray(sourceContext)
    || !Array.isArray(redirectChain) || !Array.isArray(quoteSpan)
    || quoteSpan.length !== 3) {
    throw new Error("verification repository source context is invalid");
  }
  const sourceContextSha256 = sha256Bytes(Buffer.from(row.source_context_json, "utf8"));
  const identityPayload = {
    canonical_publisher_id: row.canonical_publisher_id,
    canonical_document_id: row.canonical_document_id,
    origin_event_id: row.origin_event_id,
    canonical_url_key: row.canonical_url_key,
    redirect_chain: redirectChain,
    relation_type: row.relation_type,
    provenance_state: row.provenance_state,
    origin_canonical_url: row.origin_canonical_url,
    policy_id: row.policy_id,
    policy_sha256: row.policy_sha256,
    governance_bundle_id: row.governance_bundle_id,
    governance_bundle_sha256: row.source_governance_bundle_sha256,
    resolver_receipt_id: row.resolver_receipt_id,
    resolver_receipt_sha256: row.resolver_receipt_sha256,
  };
  const identityPayloadSha256 = sha256Bytes(Buffer.from(canonicalJson(identityPayload), "utf8"));
  const rawBytes = Buffer.from(row.source_raw_bytes);
  const normalizedContent = String(row.normalized_content ?? "");
  const [quoteStart, quoteEnd, exactQuote] = quoteSpan;
  const normalizedContentSha256 = sha256Bytes(
    Buffer.from(normalizeSourceText(normalizedContent), "utf8"),
  );
  const quoteSpanSha256 = typeof exactQuote === "string"
    ? sha256Bytes(Buffer.from(normalizeSourceText(exactQuote), "utf8"))
    : null;
  const versionMaterial = {
    source_identity_id: row.source_identity_id,
    retrieved_at: row.version_retrieved_at,
    raw_bytes_sha256: row.source_raw_bytes_sha256,
    normalized_content_sha256: row.normalized_content_sha256,
    quote_span_sha256: row.quote_span_sha256,
    version_sequence: row.version_sequence,
  };
  const expectedVersionId = `source_version_${sha256Bytes(
    Buffer.from(canonicalJson(versionMaterial), "utf8"),
  ).slice(0, 24)}`;
  let contextRawBytes;
  try {
    contextRawBytes = Buffer.from(sourceContext.original_document_bytes_base64, "base64");
  } catch {
    throw new Error("verification repository source context bytes are invalid");
  }
  const pointSlice = Number.isInteger(quoteStart) && Number.isInteger(quoteEnd)
    ? Array.from(normalizedContent).slice(quoteStart, quoteEnd).join("")
    : null;
  const valid = (
    sourceContextSha256 === row.source_context_sha256
    && sourceContextSha256 === row.rec_source_context_sha256
    && row.source_identity_id === row.rec_source_identity_id
    && row.source_version_id === row.rec_source_version_id
    && row.version_source_identity_id === row.source_identity_id
    && row.identity_payload_sha256 === identityPayloadSha256
    && row.source_identity_id === `source_identity_${identityPayloadSha256.slice(0, 24)}`
    && row.relation_type === "original"
    && row.provenance_state === "verified"
    && row.policy_id === governance.sourceIdentityPolicy.policy_id
    && row.policy_sha256 === governance.sourceIdentityPolicySha256
    && row.governance_bundle_id === governance.bundleId
    && row.source_governance_bundle_sha256 === governance.bundleSha256
    && typeof row.resolver_receipt_id === "string"
    && row.resolver_receipt_id.length > 0
    && /^[0-9a-f]{64}$/.test(row.resolver_receipt_sha256 ?? "")
    && sha256Bytes(rawBytes) === row.source_raw_bytes_sha256
    && normalizedContentSha256 === row.normalized_content_sha256
    && quoteSpanSha256 === row.quote_span_sha256
    && row.source_version_id === expectedVersionId
    && Number.isInteger(quoteStart) && quoteStart >= 0
    && Number.isInteger(quoteEnd) && quoteEnd > quoteStart
    && pointSlice === exactQuote
    && contextRawBytes.length > 0
    && contextRawBytes.equals(rawBytes)
    && sourceContext.source_identity_id === row.source_identity_id
    && sourceContext.source_version_id === row.source_version_id
    && sourceContext.canonical_url === row.canonical_url_key
    && sourceContext.canonical_publisher_id === row.canonical_publisher_id
    && sourceContext.canonical_document_id === row.canonical_document_id
    && sourceContext.origin_event_id === row.origin_event_id
    && sourceContext.source_relation === row.relation_type
    && sourceContext.source_provenance === row.provenance_state
    && sourceContext.source_identity_policy_id === row.policy_id
    && sourceContext.source_identity_policy_sha256 === row.policy_sha256
    && sourceContext.source_governance_bundle_id === row.governance_bundle_id
    && sourceContext.source_governance_bundle_sha256 === row.source_governance_bundle_sha256
    && sourceContext.source_resolver_receipt_id === row.resolver_receipt_id
    && sourceContext.source_resolver_receipt_sha256 === row.resolver_receipt_sha256
    && sourceContext.version_sequence === row.version_sequence
    && sourceContext.original_document_text === normalizedContent
    && sourceContext.raw_bytes_sha256 === row.source_raw_bytes_sha256
    && sourceContext.normalized_content_sha256 === row.normalized_content_sha256
    && sourceContext.quote_start === quoteStart
    && sourceContext.quote_end === quoteEnd
    && sourceContext.exact_quote === exactQuote
    && sourceContext.exact_quote_sha256 === sha256Bytes(Buffer.from(exactQuote, "utf8"))
  );
  if (!valid) throw new Error("verification repository source lineage failed");
  return sourceContext;
}

export function loadRepositoryVerificationAuthority({
  repositoryPath,
  runId,
  evidencePackId,
  runtimeGovernanceBundlePath,
  expectedRuntimeGovernanceBundleId,
  expectedRuntimeGovernanceBundleSha256,
}) {
  if (![repositoryPath, runId, evidencePackId].every(
    (value) => typeof value === "string" && value.trim().length > 0,
  )) throw new Error("repository verification authority inputs are incomplete");
  const governance = loadRuntimeGovernanceAuthority({
    runtimeGovernanceBundlePath,
    expectedRuntimeGovernanceBundleId,
    expectedRuntimeGovernanceBundleSha256,
  });
  const resolvedRepositoryPath = path.resolve(repositoryPath);
  if (!fs.existsSync(resolvedRepositoryPath)) {
    throw new Error("verification repository is required");
  }
  const database = new DatabaseSync(resolvedRepositoryPath, { readOnly: true });
  let rows;
  try {
    database.exec("PRAGMA query_only = ON");
    const integrity = database.prepare("PRAGMA integrity_check").get();
    if (integrity?.integrity_check !== "ok") {
      throw new Error("verification repository integrity check failed");
    }
    rows = database.prepare(`
      SELECT req.request_record_id, req.run_id, req.assertion_id,
             req.evidence_id, req.assertion_sha256,
             req.governance_bundle_sha256,
             req.producer_execution_id, req.verifier_execution_id,
             req.source_identity_id, req.source_version_id,
             req.source_context_json, req.source_context_sha256,
             raw.response_record_id,
             raw.request_record_id AS raw_request_record_id,
             raw.run_id AS raw_run_id, raw.verifier, raw.provider_trace_id,
             raw.http_status, raw.raw_body, raw.raw_response_sha256,
             raw.retrieved_at AS raw_retrieved_at,
             execution.execution_claim_id,
             execution.provider_identity AS execution_provider_identity,
             execution.provider_account_or_route_identity AS execution_route_identity,
             execution.upstream_trace_id AS execution_upstream_trace_id,
             execution.raw_response_sha256 AS execution_raw_response_sha256,
             execution.consumer_kind AS execution_consumer_kind,
             execution.consumer_record_id AS execution_consumer_record_id,
             execution.claimed_at AS execution_claimed_at,
             result.result_record_id,
             result.request_record_id AS result_request_record_id,
             result.response_record_id AS result_response_record_id,
             result.run_id AS result_run_id,
             result.assertion_id AS result_assertion_id,
             result.assertion_sha256 AS result_assertion_sha256,
             result.decision,
             rec.receipt_id,
             rec.request_record_id AS rec_request_record_id,
             rec.response_record_id AS rec_response_record_id,
             rec.result_record_id AS rec_result_record_id,
             rec.run_id AS rec_run_id,
             rec.assertion_id AS rec_assertion_id,
             rec.governance_bundle_sha256 AS rec_governance_bundle_sha256,
             rec.source_identity_id AS rec_source_identity_id,
             rec.source_version_id AS rec_source_version_id,
             rec.source_context_sha256 AS rec_source_context_sha256,
             identity.canonical_publisher_id,
             identity.canonical_document_id, identity.origin_event_id,
             identity.canonical_url_key, identity.redirect_chain_json,
             identity.relation_type, identity.provenance_state,
             identity.origin_canonical_url, identity.policy_id,
             identity.policy_sha256, identity.governance_bundle_id,
             identity.governance_bundle_sha256 AS source_governance_bundle_sha256,
             identity.resolver_receipt_id, identity.resolver_receipt_sha256,
             identity.identity_payload_sha256,
             version.source_identity_id AS version_source_identity_id,
             version.retrieved_at AS version_retrieved_at,
             version.raw_bytes AS source_raw_bytes,
             version.raw_bytes_sha256 AS source_raw_bytes_sha256,
             version.normalized_content,
             version.normalized_content_sha256,
             version.quote_span_json, version.quote_span_sha256,
             version.version_sequence
        FROM theme_chokepoint_fact_verification_reconciliations AS rec
        JOIN theme_chokepoint_fact_verification_requests AS req
          ON req.request_record_id = rec.request_record_id
        JOIN theme_chokepoint_fact_verification_raw_responses AS raw
          ON raw.response_record_id = rec.response_record_id
         AND raw.request_record_id = req.request_record_id
        LEFT JOIN theme_chokepoint_provider_executions AS execution
          ON execution.consumer_record_id = raw.response_record_id
         AND execution.consumer_kind = 'business_fact_verification'
        JOIN theme_chokepoint_fact_verification_results AS result
          ON result.result_record_id = rec.result_record_id
         AND result.request_record_id = req.request_record_id
         AND result.response_record_id = raw.response_record_id
        JOIN theme_chokepoint_source_identities AS identity
          ON identity.source_identity_id = req.source_identity_id
        JOIN theme_chokepoint_source_versions AS version
          ON version.source_version_id = req.source_version_id
         AND version.source_identity_id = identity.source_identity_id
       WHERE rec.run_id = ? AND rec.governance_bundle_sha256 = ?
       ORDER BY req.assertion_id, rec.receipt_id
    `).all(runId, governance.bundleSha256);
  } finally {
    database.close();
  }
  const ledger = {
    schema_version: "theme-chokepoint-verification-ledger-v2",
    evidence_pack_id: evidencePackId,
    requests: [],
    raw_responses: [],
    results: [],
    reconciliations: [],
  };
  const seenAssertions = new Set();
  for (const row of rows) {
    const rawBytes = Buffer.from(row.raw_body);
    const sourceContext = validateRepositorySourceLineage(row, governance);
    let rawPayload;
    try {
      rawPayload = JSON.parse(rawBytes.toString("utf8"));
    } catch {
      throw new Error("verification repository raw response is invalid");
    }
    const validExecutionOrigin = (
      Number.isInteger(row.execution_claim_id)
      && row.execution_provider_identity === row.verifier
      && row.execution_route_identity === row.verifier_execution_id
      && row.execution_upstream_trace_id === row.provider_trace_id
      && row.execution_raw_response_sha256 === row.raw_response_sha256
      && row.execution_consumer_kind === "business_fact_verification"
      && row.execution_consumer_record_id === row.response_record_id
      && row.execution_claimed_at === row.raw_retrieved_at
    );
    if (!validExecutionOrigin) {
      throw new Error("verification repository durable provider execution origin failed");
    }
    const valid = (
      row.run_id === runId && row.raw_run_id === runId
      && row.result_run_id === runId && row.rec_run_id === runId
      && row.raw_request_record_id === row.request_record_id
      && row.result_request_record_id === row.request_record_id
      && row.result_response_record_id === row.response_record_id
      && row.rec_request_record_id === row.request_record_id
      && row.rec_response_record_id === row.response_record_id
      && row.rec_result_record_id === row.result_record_id
      && row.result_assertion_id === row.assertion_id
      && row.rec_assertion_id === row.assertion_id
      && row.result_assertion_sha256 === row.assertion_sha256
      && row.governance_bundle_sha256 === governance.bundleSha256
      && row.rec_governance_bundle_sha256 === governance.bundleSha256
      && row.verifier === row.verifier_execution_id
      && row.producer_execution_id !== row.verifier_execution_id
      && createHash("sha256").update(rawBytes).digest("hex") === row.raw_response_sha256
      && rawPayload.assertion_id === row.assertion_id
      && rawPayload.assertion_sha256 === row.assertion_sha256
      && rawPayload.decision === row.decision
    );
    if (!valid || seenAssertions.has(row.assertion_id)) {
      throw new Error("verification repository reconciliation failed");
    }
    seenAssertions.add(row.assertion_id);
    ledger.requests.push({
      request_record_id: row.request_record_id,
      evidence_pack_id: evidencePackId,
      assertion_id: row.assertion_id,
      evidence_id: row.evidence_id,
      assertion_sha256: row.assertion_sha256,
      governance_bundle_sha256: governance.bundleSha256,
      producer_execution_id: row.producer_execution_id,
      verifier_execution_id: row.verifier_execution_id,
      source_identity_id: row.source_identity_id,
      source_version_id: row.source_version_id,
      source_context_sha256: row.source_context_sha256,
      source_context: sourceContext,
    });
    ledger.raw_responses.push({
      response_record_id: row.response_record_id,
      request_record_id: row.request_record_id,
      evidence_pack_id: evidencePackId,
      verifier_execution_id: row.verifier_execution_id,
      provider_trace_id: row.provider_trace_id,
      http_status: row.http_status,
      raw_body_base64: rawBytes.toString("base64"),
      raw_response_sha256: row.raw_response_sha256,
    });
    ledger.results.push({
      result_record_id: row.result_record_id,
      request_record_id: row.request_record_id,
      response_record_id: row.response_record_id,
      evidence_pack_id: evidencePackId,
      assertion_id: row.assertion_id,
      assertion_sha256: row.assertion_sha256,
      decision: row.decision,
    });
    ledger.reconciliations.push({
      reconciliation_receipt_id: row.receipt_id,
      request_record_id: row.request_record_id,
      response_record_id: row.response_record_id,
      result_record_id: row.result_record_id,
      evidence_pack_id: evidencePackId,
      assertion_id: row.assertion_id,
      governance_bundle_sha256: governance.bundleSha256,
      source_identity_id: row.source_identity_id,
      source_version_id: row.source_version_id,
      source_context_sha256: row.source_context_sha256,
    });
  }
  const authority = deepFreeze({
    repositoryPath: resolvedRepositoryPath,
    runId,
    evidencePackId,
    ledger,
    governanceBundleSha256: governance.bundleSha256,
    decisionTable: governance.decisionTable,
  });
  TRUSTED_VERIFICATION_AUTHORITIES.add(authority);
  return authority;
}

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

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function assertionProposalSha256(assertion) {
  const proposal = structuredClone(assertion);
  delete proposal.verification;
  return createHash("sha256").update(canonicalJson(proposal)).digest("hex");
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
  if (predicate.op === "context_is") {
    if (typeof predicate.field !== "string" || predicate.field.length === 0) errors.push(`${prefix}:predicate_context_field_required`);
    if (typeof predicate.value !== "boolean") errors.push(`${prefix}:predicate_context_boolean_required`);
    return;
  }
  if (predicate.op === "context_gte") {
    if (typeof predicate.field !== "string" || predicate.field.length === 0) errors.push(`${prefix}:predicate_context_field_required`);
    if (!Number.isFinite(predicate.value)) errors.push(`${prefix}:predicate_context_number_required`);
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
  if (expression.op === "context_is") {
    if (typeof expression.field !== "string" || expression.field.length === 0) errors.push(`${stateId}:state_expression_context_field_required`);
    if (typeof expression.value !== "boolean") errors.push(`${stateId}:state_expression_context_boolean_required`);
    return;
  }
  if (expression.op === "context_gte") {
    if (typeof expression.field !== "string" || expression.field.length === 0) errors.push(`${stateId}:state_expression_context_field_required`);
    if (!Number.isFinite(expression.value)) errors.push(`${stateId}:state_expression_context_number_required`);
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
  for (const familyName of Object.keys(canonicalContract.score_families ?? {})) {
    if (!task.semantic_contract?.score_families?.[familyName]) {
      errors.push(`semantic_contract:missing_family:${familyName}`);
    }
  }
  if (JSON.stringify(task.semantic_contract?.replacement_anchor_profiles ?? {}) !== JSON.stringify(canonicalContract.replacement_anchor_profiles ?? {})) {
    errors.push("semantic_contract:replacement_anchor_profiles_mismatch");
  }
  for (const [familyName, family] of Object.entries(task.semantic_contract?.score_families ?? {})) {
    const canonicalFamily = canonicalContract.score_families?.[familyName];
    if (!canonicalFamily) {
      errors.push(`semantic_contract:unknown_family:${familyName}`);
      continue;
    }
    const expectedWeights = canonicalFamily.weighted_dimensions ?? {};
    const expectedRequirements = canonicalFamily.supported_evidence_requirements ?? {};
    if (canonicalFamily.state_policy
      && JSON.stringify(family.state_policy ?? null) !== JSON.stringify(canonicalFamily.state_policy)) {
      errors.push(`semantic_contract:state_policy_mismatch:${familyName}`);
    }
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
  const canonicalGatePredicates = canonicalContract.hard_gate_predicates ?? {};
  for (const gate of task.hard_gate_tasks ?? []) {
    const expected = canonicalGatePredicates[gate.gate];
    if (expected && JSON.stringify(gate.predicate ?? null) !== JSON.stringify(expected)) {
      errors.push(`semantic_contract:hard_gate_predicate_mismatch:${gate.gate}`);
    }
  }
}

export function validateTaskContract(task, canonicalContract = null) {
  const errors = [];
  if (!Array.isArray(task.cases) || task.cases.length === 0) errors.push("task:case_required");
  if (!Array.isArray(task.ordinal_tasks) || task.ordinal_tasks.length === 0) errors.push("task:ordinal_task_required");
  if (!Array.isArray(task.hard_gate_tasks) || task.hard_gate_tasks.length === 0) errors.push("task:hard_gate_task_required");
  if (!Array.isArray(task.state_tasks) || task.state_tasks.length === 0) errors.push("task:state_task_required");
  const identityGroups = [
    ["case", task.cases ?? [], "case_id"],
    ["ordinal_item", task.ordinal_tasks ?? [], "item_id"],
    ["hard_gate_item", task.hard_gate_tasks ?? [], "item_id"],
    ["state_item", task.state_tasks ?? [], "item_id"],
  ];
  for (const [label, items, key] of identityGroups) {
    const seen = new Set();
    for (const item of items) {
      const identity = item?.[key];
      if (typeof identity !== "string" || identity.length === 0) {
        errors.push(`task:${label}_id_required`);
      } else if (seen.has(identity)) {
        errors.push(`task:duplicate_${label}_id:${identity}`);
      }
      seen.add(identity);
    }
  }
  const ordinalById = new Map((task.ordinal_tasks ?? []).map((item) => [item.item_id, item]));
  const gateById = new Map((task.hard_gate_tasks ?? []).map((item) => [item.item_id, item]));
  const caseById = new Map((task.cases ?? []).map((item) => [item.case_id, item]));

  if (!task.semantic_contract?.version) errors.push("semantic_contract:missing_version");
  validateCanonicalBinding(task, canonicalContract, errors);

  for (const item of task.ordinal_tasks ?? []) {
    if (!caseById.has(item.case_id)) errors.push(`${item.item_id}:unknown_case:${item.case_id ?? "missing"}`);
  }
  for (const item of task.hard_gate_tasks ?? []) {
    if (!caseById.has(item.case_id)) errors.push(`${item.item_id}:unknown_case:${item.case_id ?? "missing"}`);
  }
  for (const item of task.state_tasks ?? []) {
    if (!caseById.has(item.case_id)) errors.push(`${item.item_id}:unknown_case:${item.case_id ?? "missing"}`);
  }

  for (const item of task.cases ?? []) {
    if (item.assessment_track !== "replacement") continue;
    if (!['product', 'service'].includes(item.replacement_mode)) errors.push(`${item.case_id}:replacement_mode_required`);
  }
  for (const item of task.ordinal_tasks ?? []) {
    if (item.score_family !== "replacement") continue;
    const caseItem = caseById.get(item.case_id);
    const mode = caseItem?.replacement_mode;
    if (!['product', 'service'].includes(mode)) continue;
    const expected = task.semantic_contract?.replacement_anchor_profiles?.[mode]?.[item.dimension];
    if (!expected) {
      errors.push(`${item.item_id}:missing_anchor_profile_contract:${mode}:${item.dimension}`);
      continue;
    }
    if (item.anchor_profile !== expected) {
      errors.push(`${item.item_id}:anchor_profile_mismatch:expected_${expected}:actual_${item.anchor_profile ?? "missing"}`);
    }
  }

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
    const requiredHardGates = canonicalContract?.score_families?.[
      stateTask.state_family
    ]?.required_hard_gates;
    if (Array.isArray(requiredHardGates)) {
      for (const gateName of requiredHardGates) {
        if (!gateNames.has(gateName)) {
          errors.push(`${stateTask.item_id}:missing_required_hard_gate:${gateName}`);
        }
      }
      for (const gateName of gateNames) {
        if (!requiredHardGates.includes(gateName)) {
          errors.push(`${stateTask.item_id}:unexpected_hard_gate:${gateName}`);
        }
      }
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

function evaluateOrdinalPredicate(predicate, ordinalByDimension, stateContext = {}) {
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
  if (predicate.op === "context_is") {
    if (!Object.hasOwn(stateContext, predicate.field)) return "unknown";
    return stateContext[predicate.field] === predicate.value ? "pass" : "fail";
  }
  if (predicate.op === "context_gte") {
    if (!Number.isFinite(stateContext[predicate.field])) return "unknown";
    return stateContext[predicate.field] >= predicate.value ? "pass" : "fail";
  }
  if (["and", "or"].includes(predicate.op)) {
    const values = predicate.args.map((child) => evaluateOrdinalPredicate(child, ordinalByDimension, stateContext));
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

function evaluateStateExpression(expression, metrics, ordinalByDimension, gateLabelsByName, stateContext = {}) {
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
  if (expression.op === "context_is") return stateContext[expression.field] === expression.value;
  if (expression.op === "context_gte") return Number.isFinite(stateContext[expression.field]) && stateContext[expression.field] >= expression.value;
  if (expression.op === "and") return Array.isArray(expression.args) && expression.args.every((child) => evaluateStateExpression(child, metrics, ordinalByDimension, gateLabelsByName, stateContext));
  if (expression.op === "or") return Array.isArray(expression.args) && expression.args.some((child) => evaluateStateExpression(child, metrics, ordinalByDimension, gateLabelsByName, stateContext));
  if (expression.op === "not") return !evaluateStateExpression(expression.arg, metrics, ordinalByDimension, gateLabelsByName, stateContext);
  return false;
}

function deriveOrderedState(policy, metrics, ordinalByDimension, gateLabelsByName, stateContext) {
  if (!evaluateStateExpression(policy.eligibility, metrics, ordinalByDimension, gateLabelsByName, stateContext)) return null;
  for (const rule of policy.rules ?? []) {
    if (evaluateStateExpression(rule.when, metrics, ordinalByDimension, gateLabelsByName, stateContext)) return rule.state;
  }
  return null;
}

function deriveState(family, metrics, ordinalByDimension, gateLabelsByName, stateContext) {
  if (family.state_policy.type === "earnings_v1.3") {
    return deriveEarningsState(family.state_policy, metrics, ordinalByDimension);
  }
  if (family.state_policy.type === "ordered_rules_v1.3") {
    return deriveOrderedState(family.state_policy, metrics, ordinalByDimension, gateLabelsByName, stateContext);
  }
  return null;
}

function capabilitiesForEvidence(
  pack,
  verificationAuthority,
) {
  const authority = TRUSTED_VERIFICATION_AUTHORITIES.has(verificationAuthority)
    ? verificationAuthority
    : null;
  const verificationLedger = authority?.ledger ?? {};
  const decisionTable = authority?.decisionTable ?? BUSINESS_FACT_DECISION_TABLE;
  const governanceBundleSha256 = authority?.governanceBundleSha256 ?? null;
  const ledgerByAssertion = reconcileVerificationLedger(
    verificationLedger,
    pack.evidence_pack_id ?? verificationLedger?.evidence_pack_id,
    decisionTable,
    governanceBundleSha256,
  );
  return new Map((pack.evidence ?? []).map((item) => [
    item.evidence_id,
    new Set((item.claim_capabilities ?? []).filter((capability) => (
      evidenceAuthorizesCapability(
        item,
        capability,
        ledgerByAssertion,
        decisionTable,
        governanceBundleSha256,
      )
    ))),
  ]));
}

export function evidenceAuthorizesBusinessCapability({
  evidence,
  capability,
  verificationAuthority = null,
  evidencePackId = verificationAuthority?.evidencePackId,
}) {
  return capabilitiesForEvidence(
    { evidence_pack_id: evidencePackId, evidence: [evidence] },
    verificationAuthority,
  ).get(evidence.evidence_id)?.has(capability) ?? false;
}

function uniqueRecordMap(records, idField) {
  const byId = new Map();
  const duplicates = new Set();
  for (const record of records ?? []) {
    const id = record?.[idField];
    if (typeof id !== "string" || id.length === 0 || byId.has(id)) {
      if (typeof id === "string") duplicates.add(id);
      continue;
    }
    byId.set(id, record);
  }
  for (const id of duplicates) byId.delete(id);
  return byId;
}

function decodeBoundRawBytes(record) {
  if (typeof record?.raw_body_base64 !== "string" || record.raw_body_base64.length === 0) {
    return null;
  }
  const bytes = Buffer.from(record.raw_body_base64, "base64");
  if (bytes.length === 0 || bytes.toString("base64") !== record.raw_body_base64) return null;
  if (createHash("sha256").update(bytes).digest("hex") !== record.raw_response_sha256) {
    return null;
  }
  try {
    const payload = JSON.parse(bytes.toString("utf8"));
    return payload && typeof payload === "object" && !Array.isArray(payload)
      ? payload
      : null;
  } catch {
    return null;
  }
}

function reconcileVerificationLedger(
  ledger,
  evidencePackId,
  decisionTable,
  governanceBundleSha256,
) {
  if (!ledger || Array.isArray(ledger) || typeof ledger !== "object") return new Map();
  if (ledger.schema_version !== "theme-chokepoint-verification-ledger-v2") return new Map();
  if (!evidencePackId || ledger.evidence_pack_id !== evidencePackId) return new Map();
  const requests = uniqueRecordMap(ledger.requests, "request_record_id");
  const raws = uniqueRecordMap(ledger.raw_responses, "response_record_id");
  const results = uniqueRecordMap(ledger.results, "result_record_id");
  const reconciliations = uniqueRecordMap(
    ledger.reconciliations,
    "reconciliation_receipt_id",
  );
  if (![ledger.requests, ledger.raw_responses, ledger.results, ledger.reconciliations]
    .every(Array.isArray)) return new Map();
  const byAssertion = new Map();
  const duplicateAssertions = new Set();
  for (const reconciliation of reconciliations.values()) {
    const request = requests.get(reconciliation.request_record_id);
    const raw = raws.get(reconciliation.response_record_id);
    const result = results.get(reconciliation.result_record_id);
    if (!request || !raw || !result) continue;
    const rawPayload = decodeBoundRawBytes(raw);
    if (!rawPayload) continue;
    const lineageIds = [
      request.request_record_id,
      raw.response_record_id,
      result.result_record_id,
      reconciliation.reconciliation_receipt_id,
    ];
    const valid = (
      new Set(lineageIds).size === lineageIds.length
      && request.evidence_pack_id === evidencePackId
      && raw.evidence_pack_id === evidencePackId
      && result.evidence_pack_id === evidencePackId
      && reconciliation.evidence_pack_id === evidencePackId
      && raw.request_record_id === request.request_record_id
      && result.request_record_id === request.request_record_id
      && result.response_record_id === raw.response_record_id
      && reconciliation.assertion_id === request.assertion_id
      && result.assertion_id === request.assertion_id
      && result.assertion_sha256 === request.assertion_sha256
      && request.governance_bundle_sha256 === governanceBundleSha256
      && reconciliation.governance_bundle_sha256 === governanceBundleSha256
      && reconciliation.source_identity_id === request.source_identity_id
      && reconciliation.source_version_id === request.source_version_id
      && reconciliation.source_context_sha256 === request.source_context_sha256
      && request.source_context
      && typeof request.source_context === "object"
      && !Array.isArray(request.source_context)
      && typeof request.producer_execution_id === "string"
      && typeof request.verifier_execution_id === "string"
      && request.producer_execution_id.length > 0
      && request.verifier_execution_id.length > 0
      && request.producer_execution_id !== request.verifier_execution_id
      && raw.verifier_execution_id === request.verifier_execution_id
      && typeof raw.provider_trace_id === "string"
      && raw.provider_trace_id.length > 0
      && Number.isInteger(raw.http_status)
      && raw.http_status >= 200
      && raw.http_status < 300
      && rawPayload.assertion_id === request.assertion_id
      && rawPayload.assertion_sha256 === request.assertion_sha256
      && rawPayload.decision === result.decision
      && decisionTable.allowed_verification_decisions.includes(result.decision)
    );
    if (!valid) continue;
    if (byAssertion.has(request.assertion_id)) duplicateAssertions.add(request.assertion_id);
    byAssertion.set(request.assertion_id, {
      ...request,
      response_record_id: raw.response_record_id,
      result_record_id: result.result_record_id,
      reconciliation_receipt_id: reconciliation.reconciliation_receipt_id,
      raw_response_sha256: raw.raw_response_sha256,
      decision: result.decision,
    });
  }
  for (const assertionId of duplicateAssertions) byAssertion.delete(assertionId);
  return byAssertion;
}

function evidenceAuthorizesCapability(
  evidence,
  capability,
  ledgerByAssertion,
  decisionTable,
  governanceBundleSha256,
) {
  const conditionId = `capability.${capability}.v1.4`;
  if (!(evidence.condition_ids ?? []).includes(conditionId)) return false;
  if (decisionTable.unstructured_exempt_capabilities.includes(capability)) {
    return String(evidence.exact_quote ?? "").trim().length > 0;
  }
  return (evidence.business_fact_assertions ?? []).some((assertion) => (
    assertionAuthorizesCapability(
      assertion,
      evidence,
      capability,
      ledgerByAssertion,
      decisionTable,
      governanceBundleSha256,
    )
  ));
}

function assertionAuthorizesCapability(
  assertion,
  evidence,
  capability,
  ledgerByAssertion,
  decisionTable,
  governanceBundleSha256,
) {
  const verification = ledgerByAssertion.get(assertion?.assertion_id);
  if (!verification) return false;
  if (!governanceBundleSha256) return false;
  if (verification.governance_bundle_sha256 !== governanceBundleSha256) return false;
  if (verification.assertion_sha256 !== assertionProposalSha256(assertion)) return false;
  const lineageIds = [
    verification.request_record_id,
    verification.response_record_id,
    verification.result_record_id,
    verification.reconciliation_receipt_id,
  ];
  if (!lineageIds.every((value) => typeof value === "string" && value.length > 0)) return false;
  if (new Set(lineageIds).size !== lineageIds.length) return false;
  if (!verification.producer_execution_id || !verification.verifier_execution_id) return false;
  if (verification.producer_execution_id === verification.verifier_execution_id) return false;
  if (!/^[0-9a-f]{64}$/.test(verification.raw_response_sha256 ?? "")) return false;
  if (!decisionTable.allowed_verification_decisions.includes(verification.decision)) return false;
  if (!decisionTable.allowed_polarities.includes(assertion.polarity)) return false;
  if (!decisionTable.allowed_lifecycle_states.includes(assertion.lifecycle_state)) return false;
  if (assertion.evidence_id !== evidence.evidence_id) return false;
  if (assertion.canonical_predicate_id !== `capability.${capability}.v1.4`) return false;
  const evidenceCompanyId = evidence.subject_company_id
    ?? evidence.assessment_scope?.company_id;
  const evidenceProductId = evidence.subject_product_id
    ?? evidence.assessment_scope?.product_id;
  if (assertion.subject_company_id !== evidenceCompanyId) return false;
  if (assertion.subject_product_id !== evidenceProductId) return false;
  const quote = String(evidence.exact_quote ?? "");
  if (assertion.quote_start !== 0 || assertion.quote_end !== quote.length) return false;
  if (assertion.exact_quote_sha256 !== createHash("sha256").update(quote).digest("hex")) return false;
  const sourceContext = verification.source_context;
  if (!sourceContext || typeof sourceContext !== "object") return false;
  if (sourceContext.source_identity_id !== verification.source_identity_id) return false;
  if (sourceContext.source_version_id !== verification.source_version_id) return false;
  if (sourceContext.issuer_company_id !== assertion.subject_company_id) return false;
  if (sourceContext.product_id !== assertion.subject_product_id) return false;
  if (sourceContext.fiscal_period_id !== assertion.fiscal_period_id) return false;
  if (sourceContext.accounting_metric !== assertion.accounting_metric) return false;
  if (sourceContext.quote_start !== assertion.quote_start) return false;
  if (sourceContext.quote_end !== assertion.quote_end) return false;
  if (sourceContext.exact_quote !== quote) return false;
  if (sourceContext.exact_quote_sha256 !== assertion.exact_quote_sha256) return false;
  if (sourceContext.source_type !== String(evidence.source_type ?? "").toLocaleLowerCase()) {
    return false;
  }
  if (decisionTable.accounting_capabilities.includes(capability)) {
    const sourceType = String(evidence.source_type ?? "").toLocaleLowerCase();
    if (!decisionTable.official_accounting_source_markers.includes(sourceType)) {
      return false;
    }
    if (!decisionTable.required_accounting_fields.every(
      (field) => assertion[field] !== null && assertion[field] !== undefined && assertion[field] !== "",
    )) return false;
  }
  return true;
}

function sameNumberSet(left, right) {
  return sameSet((left ?? []).map(Number), (right ?? []).map(Number));
}

function validateOrdinalBoundBasis(result, errors) {
  for (const ordinal of result.ordinal ?? []) {
    const id = ordinal.item_id;
    if (
      !Number.isInteger(ordinal.rating_min)
      || !Number.isInteger(ordinal.rating_max)
      || ordinal.rating_min < 0
      || ordinal.rating_max > 4
      || ordinal.rating_min > ordinal.rating_max
    ) {
      errors.push(`${id}:rating_must_be_integer_0_to_4`);
      continue;
    }
    const basis = ordinal.bound_basis;
    if (!basis || typeof basis !== "object") {
      errors.push(`${id}:bound_basis_required`);
      continue;
    }
    if (!Array.isArray(basis.unresolved_higher_anchors) || !Array.isArray(basis.excluded_higher_anchors)) {
      errors.push(`${id}:bound_basis_anchor_arrays_required`);
      continue;
    }
    if (ordinal.evidence_state === "unknown" && ordinal.bound_type === "none") {
      if (basis.floor_anchor !== null || basis.ceiling_anchor !== null || basis.exact_basis !== null) {
        errors.push(`${id}:bound_basis_unknown_must_be_empty`);
      }
      continue;
    }
    if (ordinal.bound_type === "exact") {
      const rating = ordinal.rating_min;
      if (ordinal.rating_max !== rating || basis.floor_anchor !== rating || basis.ceiling_anchor !== rating) {
        errors.push(`${id}:bound_basis_exact_rating_mismatch`);
      }
      if (rating === 4) {
        if (basis.exact_basis !== "natural_cap") errors.push(`${id}:exact_4_requires_natural_cap`);
      } else {
        const expectedExcluded = Array.from({ length: 4 - rating }, (_, index) => rating + index + 1);
        if (!["direct_upper_bound", "contract_exclusivity"].includes(basis.exact_basis)
          || !sameNumberSet(basis.excluded_higher_anchors, expectedExcluded)) {
          errors.push(`${id}:exact_requires_supported_ceiling`);
        }
      }
      if (basis.unresolved_higher_anchors.length !== 0) errors.push(`${id}:exact_cannot_have_unresolved_higher_anchors`);
      continue;
    }
    if (ordinal.bound_type === "lower_bound") {
      if (basis.floor_anchor !== ordinal.rating_min || basis.ceiling_anchor !== null || basis.exact_basis !== null) {
        errors.push(`${id}:bound_basis_lower_bound_mismatch`);
      }
      if (basis.unresolved_higher_anchors.length === 0) errors.push(`${id}:lower_bound_requires_unresolved_higher_anchor`);
      continue;
    }
    if (ordinal.bound_type === "upper_bound") {
      if (basis.floor_anchor !== null || basis.ceiling_anchor !== ordinal.rating_max || basis.exact_basis !== null) {
        errors.push(`${id}:bound_basis_upper_bound_mismatch`);
      }
      continue;
    }
    if (ordinal.bound_type === "interval") {
      if (basis.floor_anchor !== ordinal.rating_min || basis.ceiling_anchor !== ordinal.rating_max || basis.exact_basis !== null) {
        errors.push(`${id}:bound_basis_interval_mismatch`);
      }
    }
  }
}

function decisiveConditionIsBound(condition, packEvidenceById, primaryEvidenceIds) {
  if (condition?.evidence_state !== "supported") return false;
  if (!Array.isArray(condition.evidence_ids) || condition.evidence_ids.length === 0) return false;
  if (!Array.isArray(condition.decisive_claim_ids) || condition.decisive_claim_ids.length === 0) return false;
  const citedClaims = new Set();
  for (const evidenceId of condition.evidence_ids) {
    if (!primaryEvidenceIds.has(evidenceId)) return false;
    const evidence = packEvidenceById.get(evidenceId);
    if (!evidence) return false;
    for (const claimId of evidence.claim_ids ?? []) citedClaims.add(claimId);
  }
  return condition.decisive_claim_ids.every((claimId) => citedClaims.has(claimId));
}

function validateDecisionEvidence(task, pack, result, errors) {
  const packEvidenceById = new Map(
    (pack.evidence ?? []).map((item) => [item.evidence_id, item]),
  );
  const ordinalTaskById = new Map(
    (task.ordinal_tasks ?? []).map((item) => [item.item_id, item]),
  );
  for (const ordinal of result.ordinal ?? []) {
    if (ordinal.evidence_state !== "supported") continue;
    const primaryEvidenceIds = new Set(ordinal.primary_evidence_ids ?? []);
    if (primaryEvidenceIds.size === 0) {
      errors.push(`${ordinal.item_id}:supported_primary_evidence_required`);
    }
    for (const evidenceId of primaryEvidenceIds) {
      const evidence = packEvidenceById.get(evidenceId);
      if (!evidence) {
        errors.push(`${ordinal.item_id}:primary_evidence_not_in_pack:${evidenceId}`);
      } else if (evidence.stance !== "supports") {
        errors.push(`${ordinal.item_id}:supported_evidence_must_have_supports_stance:${evidenceId}`);
      }
    }
    const conditions = ordinal.anchor_conditions ?? [];
    const dimension = ordinalTaskById.get(ordinal.item_id)?.dimension;
    const expectedFloorId = `${dimension}.anchor_${ordinal.rating_min}.floor`;
    const expectedCeilingId = `${dimension}.anchor_${ordinal.rating_max}.ceiling`;
    if (conditions.some((condition) => (
      condition.condition_type === "floor" && condition.condition_id !== expectedFloorId
    ))) errors.push(`${ordinal.item_id}:noncanonical_floor_condition_id`);
    if (conditions.some((condition) => (
      condition.condition_type === "ceiling" && condition.condition_id !== expectedCeilingId
    ))) errors.push(`${ordinal.item_id}:noncanonical_ceiling_condition_id`);
    const decisiveFloor = conditions.find((condition) => (
      condition.condition_type === "floor"
      && condition.condition_id === expectedFloorId
      && condition.anchor === ordinal.rating_min
      && decisiveConditionIsBound(condition, packEvidenceById, primaryEvidenceIds)
    ));
    const decisiveCeiling = conditions.find((condition) => (
      condition.condition_type === "ceiling"
      && condition.condition_id === expectedCeilingId
      && condition.anchor === ordinal.rating_max
      && sameNumberSet(
        condition.excluded_higher_anchors,
        ordinal.bound_basis?.excluded_higher_anchors,
      )
      && decisiveConditionIsBound(condition, packEvidenceById, primaryEvidenceIds)
    ));
    if (["exact", "lower_bound", "interval"].includes(ordinal.bound_type) && !decisiveFloor) {
      errors.push(`${ordinal.item_id}:${ordinal.bound_type}_requires_decisive_floor_evidence`);
    }
    const naturalCap = ordinal.bound_type === "exact"
      && ordinal.rating_max === 4
      && ordinal.bound_basis?.exact_basis === "natural_cap";
    if (["exact", "upper_bound", "interval"].includes(ordinal.bound_type)
      && !naturalCap
      && !decisiveCeiling) {
      errors.push(`${ordinal.item_id}:${ordinal.bound_type}_requires_decisive_ceiling_evidence`);
    }
  }
}

function validateEvidenceCapabilities(
  task,
  pack,
  result,
  errors,
  verificationAuthority,
) {
  const ordinalTaskById = new Map((task.ordinal_tasks ?? []).map((item) => [item.item_id, item]));
  const capabilities = capabilitiesForEvidence(
    pack,
    verificationAuthority,
  );
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

function validateAtomicFactOwnership(task, pack, result, errors) {
  const evidenceById = new Map(
    (pack.evidence ?? []).map((item) => [item.evidence_id, item]),
  );
  const taskById = new Map(
    (task.ordinal_tasks ?? []).map((item) => [item.item_id, item]),
  );
  const ownerByFact = new Map();
  const reported = new Set();
  for (const ordinal of result.ordinal ?? []) {
    if (ordinal.evidence_state !== "supported" || ordinal.rating_min < 3) continue;
    const dimension = taskById.get(ordinal.item_id)?.dimension;
    if (!dimension) continue;
    for (const evidenceId of ordinal.primary_evidence_ids ?? []) {
      const factId = evidenceById.get(evidenceId)?.atomic_fact_id;
      if (typeof factId !== "string" || factId.trim().length === 0) continue;
      const prior = ownerByFact.get(factId);
      if (prior && prior !== dimension) {
        const key = `${factId}:${prior}:${dimension}`;
        if (!reported.has(key)) {
          errors.push(
            `${ordinal.item_id}:atomic_fact_primary_high_grade_conflict:${factId}:${prior}:${dimension}`,
          );
          reported.add(key);
        }
      } else {
        ownerByFact.set(factId, dimension);
      }
    }
  }
}

function validateResultCardinality(task, result, errors) {
  const groups = [
    ["ordinal", task.ordinal_tasks ?? [], result.ordinal ?? []],
    ["hard_gate", task.hard_gate_tasks ?? [], result.hard_gate ?? []],
    ["state", task.state_tasks ?? [], result.state ?? []],
  ];
  for (const [label, taskItems, resultItems] of groups) {
    const expected = new Set(taskItems.map((item) => item.item_id));
    const counts = new Map();
    for (const item of resultItems) {
      counts.set(item.item_id, (counts.get(item.item_id) ?? 0) + 1);
      if (!expected.has(item.item_id)) {
        errors.push(`result:unexpected_${label}_item:${item.item_id ?? "missing"}`);
      }
    }
    for (const itemId of expected) {
      const count = counts.get(itemId) ?? 0;
      if (count === 0) errors.push(`result:missing_${label}_item:${itemId}`);
      if (count > 1) errors.push(`result:duplicate_${label}_item:${itemId}`);
    }
  }
}

export function validateSubmissionSemantics({
  task,
  pack,
  result,
  canonicalContract = null,
  verificationAuthority = null,
}) {
  const errors = [...validateTaskContract(task, canonicalContract)];
  validateResultCardinality(task, result, errors);
  validateOrdinalBoundBasis(result, errors);
  validateDecisionEvidence(task, pack, result, errors);
  validateEvidenceCapabilities(
    task,
    pack,
    result,
    errors,
    verificationAuthority,
  );
  validateAtomicFactOwnership(task, pack, result, errors);

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

    const stateContext = pack.state_context?.[stateTask.case_id] ?? {};
    const expectedGateLabels = new Map();
    const gateLabelsByName = new Map();
    for (const gateId of stateTask.hard_gate_item_ids ?? []) {
      const gateTask = gateTaskById.get(gateId);
      if (!gateTask) continue;
      const expected = evaluateOrdinalPredicate(gateTask.predicate, ordinalByDimension, stateContext);
      expectedGateLabels.set(gateId, expected);
      gateLabelsByName.set(gateTask.gate, expected);
      const actual = gateResultById.get(gateId)?.label;
      if (actual !== expected) errors.push(`${gateId}:semantic_label_mismatch:expected_${expected}:actual_${actual ?? "missing"}`);
    }

    const metrics = deriveMetrics(family, ordinalByDimension);
    const expectedState = deriveState(family, metrics, ordinalByDimension, gateLabelsByName, stateContext);
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
