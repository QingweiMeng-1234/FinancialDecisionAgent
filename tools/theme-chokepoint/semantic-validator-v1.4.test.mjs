import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";

import {
  evidenceAuthorizesBusinessCapability,
  loadRepositoryVerificationAuthority,
  validateSubmissionSemantics as validateSubmissionSemanticsRaw,
  validateTaskContract,
} from "./semantic-validator-v1.4.mjs";

const dimensions = [
  ["revenue_materiality", 20, ["accounting_revenue_confirmed"]],
  ["volume_realization_leverage", 15, []],
  ["pricing_power", 15, []],
  ["margin_transmission", 20, []],
  ["time_to_revenue", 10, ["accounting_revenue_confirmed"]],
  ["capital_cash_burden", 10, []],
  ["customer_concentration_exposure", 2.5, []],
  ["customer_relationship_protection", 2.5, []],
  ["earnings_persistence", 5, ["multi_period_revenue_confirmed"]],
];

function makeEarningsStatePolicy() {
  return {
    type: "earnings_v1.3",
    mandatory_dimensions: ["revenue_materiality", "margin_transmission", "time_to_revenue"],
    eligibility_decision_coverage_min: 0.65,
    hard_fail_rating_max_below: 2,
    material_score_min: 65,
    material_decision_coverage_min: 0.70,
    material_mandatory_rating_min: 2,
    weak_score_max_below: 50,
    states: {
      weak: "weak_earnings_capture",
      material: "material_earnings_path",
      moderate: "moderate_earnings_path"
    }
  };
}

function makeTask() {
  const ordinalTasks = dimensions.map(([dimension], index) => ({
    item_id: `O${index + 1}`,
    case_id: "C1",
    score_family: "earnings",
    dimension,
  }));
  return {
    task_id: "semantic-validator-fixture-v1.4",
    contract_id: "theme-chokepoint-scoring-v1.4",
    evidence_pack_id: "fixture-pack-v1.4",
    cases: [{ case_id: "C1", evidence_ids: ["E-REV", "E-PROD", "E-GENERAL"] }],
    semantic_contract: {
      version: "theme-chokepoint-semantic-task-contract-v1.4",
      score_families: {
        earnings: {
          dimensions: Object.fromEntries(dimensions.map(([name, weight, capabilities]) => [name, {
            weight,
            supported_evidence_any_of: capabilities,
          }])),
          state_policy: makeEarningsStatePolicy()
        }
      }
    },
    ordinal_tasks: ordinalTasks,
    hard_gate_tasks: [
      { item_id: "G1", case_id: "C1", gate: "earnings_revenue_non_unknown", predicate: { op: "ordinal_resolved", dimension: "revenue_materiality" } },
      { item_id: "G2", case_id: "C1", gate: "earnings_margin_min_2", predicate: { op: "ordinal_min", dimension: "margin_transmission", min: 2 } },
    ],
    state_tasks: [{
      item_id: "S1",
      case_id: "C1",
      state_family: "earnings",
      ordinal_item_ids: ordinalTasks.map((item) => item.item_id),
      hard_gate_item_ids: ["G1", "G2"],
    }],
  };
}

function supported(itemId, evidenceIds = ["E-GENERAL"]) {
  const claimIds = evidenceIds.map((evidenceId) => `claim-${evidenceId}`);
  return {
    item_id: itemId,
    evidence_state: "supported",
    bound_type: "exact",
    rating_min: 4,
    rating_max: 4,
    primary_evidence_ids: evidenceIds,
    bound_basis: {
      floor_anchor: 4,
      ceiling_anchor: 4,
      exact_basis: "natural_cap",
      unresolved_higher_anchors: [],
      excluded_higher_anchors: []
    },
    anchor_conditions: [{
      condition_id: `${dimensions[Number(itemId.slice(1)) - 1][0]}.anchor_4.floor`,
      condition_type: "floor",
      anchor: 4,
      evidence_state: "supported",
      evidence_ids: evidenceIds,
      decisive_claim_ids: claimIds,
      excluded_higher_anchors: []
    }],
    withheld_reason: null,
    rationale: "固定fixture提供完整下限和自然封顶。",
  };
}

function makeResult(task) {
  const ordinal = task.ordinal_tasks.map((item) => supported(
    item.item_id,
    ["revenue_materiality", "time_to_revenue", "earnings_persistence"].includes(item.dimension)
      ? ["E-REV"]
      : ["E-GENERAL"],
  ));
  return {
    task_id: task.task_id,
    contract_id: task.contract_id,
    evidence_pack_id: task.evidence_pack_id,
    ordinal,
    hard_gate: [
      { item_id: "G1", label: "pass", evidence_ids: ["E-REV"], withheld_reason: null, rationale: "收入维度已经由受支持证据解析。" },
      { item_id: "G2", label: "pass", evidence_ids: ["E-GENERAL"], withheld_reason: null, rationale: "利润传导下限达到合同阈值。" },
    ],
    state: [{
      item_id: "S1",
      primary_state: "material_earnings_path",
      achieved_hard_gates: ["G1", "G2"],
      derived_metrics: {
        score_min: 100,
        score_max: 100,
        presence_coverage: 1,
        resolved_coverage: 1,
        decision_coverage: 1,
      },
      withheld_reason: null,
      rationale: "全部加权维度和强制谓词均机械满足。",
    }],
  };
}

function makeBusinessAssertion(evidence, capability, overrides = {}) {
  const accounting = ["accounting_revenue_confirmed", "multi_period_revenue_confirmed"].includes(capability);
  return {
    assertion_id: `assertion-${evidence.evidence_id}-${capability}`,
    evidence_id: evidence.evidence_id,
    subject_company_id: evidence.subject_company_id,
    subject_product_id: evidence.subject_product_id,
    canonical_predicate_id: `capability.${capability}.v1.4`,
    polarity: "affirmative",
    lifecycle_state: "current",
    quote_start: 0,
    quote_end: evidence.exact_quote.length,
    exact_quote_sha256: createHash("sha256").update(evidence.exact_quote).digest("hex"),
    verification: {
      request_record_id: `verify-request-${evidence.evidence_id}`,
      response_record_id: `verify-response-${evidence.evidence_id}`,
      receipt_id: `verify-receipt-${evidence.evidence_id}`,
      verifier: "independent-business-fact-verifier-v1",
      verified_at: "2026-08-16T00:00:00Z",
      raw_response_sha256: "a".repeat(64),
      decision: "verified",
    },
    accounting_metric: accounting ? "revenue" : null,
    accounting_value: accounting ? 25 : null,
    accounting_unit: accounting ? "million" : null,
    currency: accounting ? "USD" : null,
    fiscal_period_type: accounting ? "quarter" : null,
    fiscal_period_id: accounting ? "2026Q2" : null,
    accounting_basis: accounting ? "GAAP" : null,
    ...overrides,
  };
}

const pack = {
  evidence_pack_id: "fixture-pack-v1.4",
  evidence: [
    {
      evidence_id: "E-REV",
      claim_ids: ["claim-E-REV"],
      claim_capabilities: ["accounting_revenue_confirmed", "multi_period_revenue_confirmed"],
      condition_ids: ["capability.accounting_revenue_confirmed.v1.4", "capability.multi_period_revenue_confirmed.v1.4"],
      exact_quote: "powerco ups-x accounting revenue confirmed at $25 million; multi period revenue",
      subject_company_id: "powerco",
      subject_product_id: "ups-x",
      source_type: "company_filing",
      evidence_time_semantics: "four_quarter",
      stance: "supports",
    },
    {
      evidence_id: "E-PROD",
      claim_ids: ["claim-E-PROD"],
      claim_capabilities: ["commercial_production_stage", "nonzero_ramp_output"],
      condition_ids: ["capability.commercial_production_stage.v1.4", "capability.nonzero_ramp_output.v1.4"],
      exact_quote: "challengerco ups-y commercial production stage; nonzero ramp output",
      subject_company_id: "challengerco",
      subject_product_id: "ups-y",
      source_type: "company_filing",
      stance: "supports",
    },
    {
      evidence_id: "E-GENERAL",
      claim_ids: ["claim-E-GENERAL"],
      claim_capabilities: ["general_scoring_evidence"],
      condition_ids: ["capability.general_scoring_evidence.v1.4"],
      exact_quote: "general scoring evidence",
      stance: "supports",
    },
  ],
};

for (const evidence of pack.evidence) {
  evidence.business_fact_assertions = (evidence.claim_capabilities ?? [])
    .filter((capability) => capability !== "general_scoring_evidence")
    .map((capability) => makeBusinessAssertion(evidence, capability));
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

function assertionSha256(assertion) {
  const proposal = structuredClone(assertion);
  delete proposal.verification;
  return createHash("sha256").update(canonicalJson(proposal)).digest("hex");
}

const TEST_GOVERNANCE_BUNDLE_SHA256 =
  "de5e95275285132e9d147b3d586056fbf3b75de2617b5423d28a6bc320c59e63";
const TEST_SOURCE_POLICY_ID = "theme-chokepoint-source-identity-policy-v1";
const TEST_SOURCE_POLICY_SHA256 =
  "8bf45510ca03a416f1a3d43105cb5d129547c7368583790d157dc20afae43a69";

function makeVerificationSourceLineage(
  evidence, assertion, governanceBundleSha256, governanceBundleId,
) {
  const retrievedAt = "2026-08-16T00:00:00+00:00";
  const canonicalUrl = `https://example.test/${evidence.evidence_id.toLowerCase()}`;
  const canonicalPublisherId = `issuer:${evidence.subject_company_id}`;
  const canonicalDocumentId = `document:${evidence.evidence_id}`;
  const originEventId = `origin:${evidence.evidence_id}`;
  const resolverReceiptId = `resolver:${evidence.evidence_id}`;
  const resolverReceiptSha256 = createHash("sha256").update(resolverReceiptId).digest("hex");
  const identityPayload = {
    canonical_publisher_id: canonicalPublisherId,
    canonical_document_id: canonicalDocumentId,
    origin_event_id: originEventId,
    canonical_url_key: canonicalUrl,
    redirect_chain: [canonicalUrl],
    relation_type: "original",
    provenance_state: "verified",
    origin_canonical_url: canonicalUrl,
    policy_id: TEST_SOURCE_POLICY_ID,
    policy_sha256: TEST_SOURCE_POLICY_SHA256,
    governance_bundle_id: governanceBundleId,
    governance_bundle_sha256: governanceBundleSha256,
    resolver_receipt_id: resolverReceiptId,
    resolver_receipt_sha256: resolverReceiptSha256,
  };
  const identityPayloadSha256 = createHash("sha256")
    .update(canonicalJson(identityPayload)).digest("hex");
  const sourceIdentityId = `source_identity_${identityPayloadSha256.slice(0, 24)}`;
  const rawBytes = Buffer.from(evidence.exact_quote);
  const rawBytesSha256 = createHash("sha256").update(rawBytes).digest("hex");
  const normalizedContentSha256 = createHash("sha256")
    .update(evidence.exact_quote).digest("hex");
  const quoteSpan = [0, evidence.exact_quote.length, evidence.exact_quote];
  const quoteSpanSha256 = normalizedContentSha256;
  const versionMaterial = {
    source_identity_id: sourceIdentityId,
    retrieved_at: retrievedAt,
    raw_bytes_sha256: rawBytesSha256,
    normalized_content_sha256: normalizedContentSha256,
    quote_span_sha256: quoteSpanSha256,
    version_sequence: 1,
  };
  const sourceVersionId = `source_version_${createHash("sha256")
    .update(canonicalJson(versionMaterial)).digest("hex").slice(0, 24)}`;
  const sourceContext = {
    source_identity_id: sourceIdentityId,
    source_version_id: sourceVersionId,
    canonical_url: canonicalUrl,
    canonical_publisher_id: canonicalPublisherId,
    canonical_document_id: canonicalDocumentId,
    origin_event_id: originEventId,
    source_relation: "original",
    source_provenance: "verified",
    source_identity_policy_id: TEST_SOURCE_POLICY_ID,
    source_identity_policy_sha256: TEST_SOURCE_POLICY_SHA256,
    source_governance_bundle_id: governanceBundleId,
    source_governance_bundle_sha256: governanceBundleSha256,
    source_resolver_receipt_id: resolverReceiptId,
    source_resolver_receipt_sha256: resolverReceiptSha256,
    source_type: evidence.source_type,
    version_sequence: 1,
    original_document_bytes_base64: rawBytes.toString("base64"),
    original_document_text: evidence.exact_quote,
    raw_bytes_sha256: rawBytesSha256,
    normalized_content_sha256: normalizedContentSha256,
    quote_start: assertion.quote_start,
    quote_end: assertion.quote_end,
    exact_quote: evidence.exact_quote,
    exact_quote_sha256: assertion.exact_quote_sha256,
    issuer_company_id: assertion.subject_company_id,
    product_id: assertion.subject_product_id,
    fiscal_period_id: assertion.fiscal_period_id,
    accounting_metric: assertion.accounting_metric,
  };
  const sourceContextJson = canonicalJson(sourceContext);
  return {
    identity: {
      source_identity_id: sourceIdentityId,
      ...identityPayload,
      redirect_chain_json: canonicalJson(identityPayload.redirect_chain),
      identity_payload_sha256: identityPayloadSha256,
      created_at: retrievedAt,
    },
    version: {
      source_version_id: sourceVersionId,
      source_identity_id: sourceIdentityId,
      retrieved_at: retrievedAt,
      raw_bytes: rawBytes,
      raw_bytes_sha256: rawBytesSha256,
      normalized_content: evidence.exact_quote,
      normalized_content_sha256: normalizedContentSha256,
      quote_span_json: canonicalJson(quoteSpan),
      quote_span_sha256: quoteSpanSha256,
      content_type: "text/plain",
      language: "en",
      publication_time: null,
      updated_time: null,
      version_sequence: 1,
      created_at: retrievedAt,
    },
    context: sourceContext,
    sourceContextJson,
    sourceContextSha256: createHash("sha256").update(sourceContextJson).digest("hex"),
  };
}

function makeVerificationLedger(
  evidencePack,
  governanceBundleSha256 = TEST_GOVERNANCE_BUNDLE_SHA256,
  governanceBundleId = "theme-chokepoint-evidence-trust-runtime-v1",
) {
  const ledger = {
    schema_version: "theme-chokepoint-verification-ledger-v2",
    evidence_pack_id: evidencePack.evidence_pack_id,
    requests: [],
    raw_responses: [],
    results: [],
    reconciliations: [],
    source_identities: [],
    source_versions: [],
  };
  const seenSourceIdentities = new Set();
  const seenSourceVersions = new Set();
  for (const evidence of evidencePack.evidence ?? []) {
    for (const assertion of evidence.business_fact_assertions ?? []) {
      const source = makeVerificationSourceLineage(
        evidence, assertion, governanceBundleSha256, governanceBundleId,
      );
      const assertionHash = assertionSha256(assertion);
      const requestId = `ledger-request-${assertion.assertion_id}`;
      const responseId = `ledger-response-${assertion.assertion_id}`;
      const resultId = `ledger-result-${assertion.assertion_id}`;
      const reconciliationId = `ledger-reconciliation-${assertion.assertion_id}`;
      const rawBody = Buffer.from(JSON.stringify({
        assertion_id: assertion.assertion_id,
        assertion_sha256: assertionHash,
        decision: "verified",
      }));
      ledger.requests.push({
        request_record_id: requestId,
        evidence_pack_id: evidencePack.evidence_pack_id,
        assertion_id: assertion.assertion_id,
        evidence_id: assertion.evidence_id,
        assertion_sha256: assertionHash,
        governance_bundle_sha256: governanceBundleSha256,
        producer_execution_id: "evidence-pack-producer-v1",
        verifier_execution_id: "independent-business-fact-verifier-v1",
        source_identity_id: source.identity.source_identity_id,
        source_version_id: source.version.source_version_id,
        source_context_json: source.sourceContextJson,
        source_context_sha256: source.sourceContextSha256,
      });
      ledger.raw_responses.push({
        response_record_id: responseId,
        request_record_id: requestId,
        evidence_pack_id: evidencePack.evidence_pack_id,
        verifier_execution_id: "independent-business-fact-verifier-v1",
        provider_trace_id: `provider-trace-${assertion.assertion_id}`,
        http_status: 200,
        raw_body_base64: rawBody.toString("base64"),
        raw_response_sha256: createHash("sha256").update(rawBody).digest("hex"),
      });
      ledger.results.push({
        result_record_id: resultId,
        request_record_id: requestId,
        response_record_id: responseId,
        evidence_pack_id: evidencePack.evidence_pack_id,
        assertion_id: assertion.assertion_id,
        assertion_sha256: assertionHash,
        decision: "verified",
      });
      ledger.reconciliations.push({
        reconciliation_receipt_id: reconciliationId,
        request_record_id: requestId,
        response_record_id: responseId,
        result_record_id: resultId,
        evidence_pack_id: evidencePack.evidence_pack_id,
        assertion_id: assertion.assertion_id,
        governance_bundle_sha256: governanceBundleSha256,
        source_identity_id: source.identity.source_identity_id,
        source_version_id: source.version.source_version_id,
        source_context_sha256: source.sourceContextSha256,
      });
      if (!seenSourceIdentities.has(source.identity.source_identity_id)) {
        ledger.source_identities.push(source.identity);
        seenSourceIdentities.add(source.identity.source_identity_id);
      }
      if (!seenSourceVersions.has(source.version.source_version_id)) {
        ledger.source_versions.push(source.version);
        seenSourceVersions.add(source.version.source_version_id);
      }
    }
  }
  return ledger;
}

const verificationLedger = makeVerificationLedger(pack);

function writeVerificationRepository(
  databasePath,
  ledger,
  runId = "node-semantic-test-run",
  { includeProviderExecutionOrigins = true } = {},
) {
  const database = new DatabaseSync(databasePath);
  try {
    database.exec(`
      CREATE TABLE theme_chokepoint_source_identities (
        source_identity_id TEXT PRIMARY KEY,
        canonical_publisher_id TEXT NOT NULL, canonical_document_id TEXT,
        origin_event_id TEXT, canonical_url_key TEXT NOT NULL,
        redirect_terminal_identity_id TEXT, redirect_chain_json TEXT NOT NULL,
        relation_type TEXT NOT NULL, provenance_state TEXT NOT NULL,
        origin_canonical_url TEXT, policy_id TEXT, policy_sha256 TEXT,
        governance_bundle_id TEXT, governance_bundle_sha256 TEXT,
        resolver_receipt_id TEXT, resolver_receipt_sha256 TEXT,
        identity_payload_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
      );
      CREATE TABLE theme_chokepoint_source_versions (
        source_version_id TEXT PRIMARY KEY,
        source_identity_id TEXT NOT NULL REFERENCES theme_chokepoint_source_identities(source_identity_id),
        retrieved_at TEXT NOT NULL, raw_bytes BLOB NOT NULL,
        raw_bytes_sha256 TEXT NOT NULL, normalized_content TEXT NOT NULL,
        normalized_content_sha256 TEXT NOT NULL, quote_span_json TEXT,
        quote_span_sha256 TEXT, content_type TEXT NOT NULL, language TEXT NOT NULL,
        publication_time TEXT, updated_time TEXT, version_sequence INTEGER NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE TABLE theme_chokepoint_fact_verification_requests (
        request_record_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
        assertion_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
        assertion_sha256 TEXT NOT NULL, governance_bundle_sha256 TEXT NOT NULL,
        producer_execution_id TEXT NOT NULL, verifier_execution_id TEXT NOT NULL,
        source_identity_id TEXT NOT NULL REFERENCES theme_chokepoint_source_identities(source_identity_id),
        source_version_id TEXT NOT NULL REFERENCES theme_chokepoint_source_versions(source_version_id),
        source_context_json TEXT NOT NULL, source_context_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE TABLE theme_chokepoint_provider_executions (
        execution_claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_identity TEXT NOT NULL,
        provider_account_or_route_identity TEXT NOT NULL,
        upstream_trace_id TEXT NOT NULL,
        raw_response_sha256 TEXT NOT NULL,
        consumer_kind TEXT NOT NULL,
        consumer_record_id TEXT NOT NULL UNIQUE,
        claimed_at TEXT NOT NULL,
        UNIQUE(provider_identity, upstream_trace_id),
        UNIQUE(provider_identity, raw_response_sha256)
      );
      CREATE TABLE theme_chokepoint_fact_verification_raw_responses (
        response_record_id TEXT PRIMARY KEY, request_record_id TEXT NOT NULL,
        run_id TEXT NOT NULL, verifier TEXT NOT NULL, provider_trace_id TEXT NOT NULL,
        http_status INTEGER NOT NULL, raw_body BLOB NOT NULL,
        raw_response_sha256 TEXT NOT NULL, retrieved_at TEXT NOT NULL,
        cost_usd REAL NOT NULL
      );
      CREATE TABLE theme_chokepoint_fact_verification_results (
        result_record_id TEXT PRIMARY KEY, request_record_id TEXT NOT NULL,
        response_record_id TEXT NOT NULL, run_id TEXT NOT NULL,
        assertion_id TEXT NOT NULL, assertion_sha256 TEXT NOT NULL,
        decision TEXT NOT NULL, parsed_at TEXT NOT NULL
      );
      CREATE TABLE theme_chokepoint_fact_verification_reconciliations (
        receipt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
        assertion_id TEXT NOT NULL, request_record_id TEXT NOT NULL,
        response_record_id TEXT NOT NULL, result_record_id TEXT NOT NULL,
        governance_bundle_sha256 TEXT NOT NULL,
        source_identity_id TEXT NOT NULL REFERENCES theme_chokepoint_source_identities(source_identity_id),
        source_version_id TEXT NOT NULL REFERENCES theme_chokepoint_source_versions(source_version_id),
        source_context_sha256 TEXT NOT NULL, reconciled_at TEXT NOT NULL
      );
    `);
    const insertIdentity = database.prepare(`INSERT INTO theme_chokepoint_source_identities
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
    const insertVersion = database.prepare(`INSERT INTO theme_chokepoint_source_versions
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
    const insertRequest = database.prepare(`INSERT INTO theme_chokepoint_fact_verification_requests
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
    const insertProviderExecution = database.prepare(`INSERT INTO theme_chokepoint_provider_executions(
      provider_identity, provider_account_or_route_identity, upstream_trace_id,
      raw_response_sha256, consumer_kind, consumer_record_id, claimed_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?)`);
    const insertRaw = database.prepare(`INSERT INTO theme_chokepoint_fact_verification_raw_responses
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
    const insertResult = database.prepare(`INSERT INTO theme_chokepoint_fact_verification_results
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)`);
    const insertReconciliation = database.prepare(`INSERT INTO theme_chokepoint_fact_verification_reconciliations
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
    const rawById = new Map(ledger.raw_responses.map((item) => [item.response_record_id, item]));
    const resultById = new Map(ledger.results.map((item) => [item.result_record_id, item]));
    for (const identity of ledger.source_identities) {
      insertIdentity.run(
        identity.source_identity_id, identity.canonical_publisher_id,
        identity.canonical_document_id, identity.origin_event_id,
        identity.canonical_url_key, null, identity.redirect_chain_json,
        identity.relation_type, identity.provenance_state,
        identity.origin_canonical_url, identity.policy_id, identity.policy_sha256,
        identity.governance_bundle_id, identity.governance_bundle_sha256,
        identity.resolver_receipt_id, identity.resolver_receipt_sha256,
        identity.identity_payload_sha256, identity.created_at,
      );
    }
    for (const version of ledger.source_versions) {
      insertVersion.run(
        version.source_version_id, version.source_identity_id, version.retrieved_at,
        version.raw_bytes, version.raw_bytes_sha256, version.normalized_content,
        version.normalized_content_sha256, version.quote_span_json,
        version.quote_span_sha256, version.content_type, version.language,
        version.publication_time, version.updated_time, version.version_sequence,
        version.created_at,
      );
    }
    for (const request of ledger.requests) {
      insertRequest.run(
        request.request_record_id, runId, request.assertion_id, request.evidence_id,
        request.assertion_sha256, request.governance_bundle_sha256,
        request.producer_execution_id, request.verifier_execution_id,
        request.source_identity_id, request.source_version_id,
        request.source_context_json, request.source_context_sha256,
        "2026-08-16T00:00:00+00:00",
      );
    }
    for (const raw of ledger.raw_responses) {
      if (includeProviderExecutionOrigins) {
        insertProviderExecution.run(
          raw.verifier_execution_id, raw.verifier_execution_id,
          raw.provider_trace_id, raw.raw_response_sha256,
          "business_fact_verification", raw.response_record_id,
          "2026-08-16T00:00:00+00:00",
        );
      }
      insertRaw.run(
        raw.response_record_id, raw.request_record_id, runId,
        raw.verifier_execution_id, raw.provider_trace_id, raw.http_status,
        Buffer.from(raw.raw_body_base64, "base64"), raw.raw_response_sha256,
        "2026-08-16T00:00:00+00:00", 0,
      );
    }
    for (const result of ledger.results) {
      insertResult.run(
        result.result_record_id, result.request_record_id, result.response_record_id,
        runId, result.assertion_id, result.assertion_sha256, result.decision,
        "2026-08-16T00:00:00+00:00",
      );
    }
    for (const reconciliation of ledger.reconciliations) {
      const raw = rawById.get(reconciliation.response_record_id);
      const result = resultById.get(reconciliation.result_record_id);
      assert(raw && result);
      insertReconciliation.run(
        reconciliation.reconciliation_receipt_id, runId,
        reconciliation.assertion_id, reconciliation.request_record_id,
        reconciliation.response_record_id, reconciliation.result_record_id,
        reconciliation.governance_bundle_sha256,
        reconciliation.source_identity_id, reconciliation.source_version_id,
        reconciliation.source_context_sha256, "2026-08-16T00:00:00+00:00",
      );
    }
  } finally {
    database.close();
  }
  return databasePath;
}

const semanticToolDir = path.dirname(fileURLToPath(import.meta.url));
const defaultVerificationRepositoryPath = writeVerificationRepository(
  path.join(fs.mkdtempSync(path.join(os.tmpdir(), "theme-node-authority-")), "theme.db"),
  verificationLedger,
);
const defaultVerificationAuthority = loadRepositoryVerificationAuthority({
  repositoryPath: defaultVerificationRepositoryPath,
  runId: "node-semantic-test-run",
  evidencePackId: pack.evidence_pack_id,
  runtimeGovernanceBundlePath: path.join(
    semanticToolDir, "runtime-governance-bundle-v1.json",
  ),
  expectedRuntimeGovernanceBundleId: "theme-chokepoint-evidence-trust-runtime-v1",
  expectedRuntimeGovernanceBundleSha256: TEST_GOVERNANCE_BUNDLE_SHA256,
});

function validateSubmissionSemantics(args) {
  return validateSubmissionSemanticsRaw({
    ...args,
    verificationAuthority: (
      Object.hasOwn(args, "verificationLedger")
      || Object.hasOwn(args, "governanceBundleSha256")
    ) ? null : defaultVerificationAuthority,
  });
}

function makeCanonicalContract() {
  return {
    contract_id: "theme-chokepoint-semantic-task-contract-v1.4",
    status: "implementation_candidate_unfrozen",
    inherits_scoring_contract: "theme-chokepoint-scoring-v1.4",
    score_families: {
      earnings: {
        weighted_dimensions: Object.fromEntries(dimensions.map(([name, weight]) => [name, weight])),
        required_hard_gates: ["earnings_revenue_non_unknown", "earnings_margin_min_2"],
        supported_evidence_requirements: {
          revenue_materiality: ["accounting_revenue_confirmed"],
          time_to_revenue: ["accounting_revenue_confirmed"],
          earnings_persistence: ["multi_period_revenue_confirmed"],
        },
        state_policy: makeEarningsStatePolicy(),
      },
    },
    hard_gate_predicates: {
      earnings_revenue_non_unknown: { op: "ordinal_resolved", dimension: "revenue_materiality" },
      earnings_margin_min_2: { op: "ordinal_min", dimension: "margin_transmission", min: 2 },
    },
  };
}

function fileSha256(filePath) {
  return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function writeRuntimeGovernanceBundle(fixtureDir, semanticContractPath) {
  const toolDir = path.dirname(fileURLToPath(import.meta.url));
  const artifacts = {
    runtime_overlay: semanticContractPath,
  };
  for (const [name, sourceName] of [
    ["business_fact_decision_table", "business-fact-decision-table-v1.4.json"],
    ["source_identity_schema", "source-identity-schema-v1.json"],
    ["source_identity_policy", "source-identity-policy-v1.json"],
  ]) {
    const destination = path.join(fixtureDir, sourceName);
    fs.copyFileSync(path.join(toolDir, sourceName), destination);
    artifacts[name] = destination;
  }
  const bundle = {
    schema_version: "theme-chokepoint-runtime-governance-bundle-v1",
    bundle_id: "theme-chokepoint-test-runtime-v1",
    status: "frozen_for_implementation",
    artifacts: Object.fromEntries(Object.entries(artifacts).map(([name, filePath]) => [
      name,
      { path: path.basename(filePath), sha256: fileSha256(filePath) },
    ])),
  };
  const bundlePath = path.join(fixtureDir, "runtime-governance-bundle.json");
  fs.writeFileSync(bundlePath, `${JSON.stringify(bundle, null, 2)}\n`);
  return { bundlePath, bundleId: bundle.bundle_id, bundleSha256: fileSha256(bundlePath) };
}

test("TD-01: state task缺少任一加权维度时拒绝任务合同", () => {
  const task = makeTask();
  task.ordinal_tasks = task.ordinal_tasks.filter((item) => item.dimension !== "capital_cash_burden");
  task.state_tasks[0].ordinal_item_ids = task.ordinal_tasks.map((item) => item.item_id);

  const errors = validateTaskContract(task);
  assert(errors.includes("S1:missing_weighted_dimension:capital_cash_burden"));
});

test("P0-02: empty and noncanonical task graphs fail closed", () => {
  const empty = {
    ...makeTask(),
    cases: [],
    ordinal_tasks: [],
    hard_gate_tasks: [],
    state_tasks: [],
  };
  const errors = validateTaskContract(empty, makeCanonicalContract());

  assert(errors.includes("task:case_required"));
  assert(errors.includes("task:ordinal_task_required"));
  assert(errors.includes("task:hard_gate_task_required"));
  assert(errors.includes("task:state_task_required"));
});

test("P0-02: ordinal ratings are integers in the closed range zero through four", () => {
  for (const invalid of [-1, 5, 1.5, "4", Number.NaN]) {
    const task = makeTask();
    const result = makeResult(task);
    result.ordinal[0] = {
      ...result.ordinal[0],
      rating_min: invalid,
      rating_max: invalid,
      bound_basis: {
        ...result.ordinal[0].bound_basis,
        floor_anchor: invalid,
        ceiling_anchor: invalid,
      },
    };

    const report = validateSubmissionSemantics({
      task,
      pack,
      result,
      canonicalContract: makeCanonicalContract(),
    });
    assert.equal(report.valid, false, `invalid rating accepted: ${String(invalid)}`);
    assert(report.errors.includes("O1:rating_must_be_integer_0_to_4"));
  }
});

test("P1-06: one atomic fact cannot receive primary high-grade credit in two dimensions", () => {
  const task = makeTask();
  const result = makeResult(task);
  const atomicPack = structuredClone(pack);
  const general = atomicPack.evidence.find((item) => item.evidence_id === "E-GENERAL");
  general.atomic_fact_id = "atomic_fact_shared_source_span";

  const report = validateSubmissionSemantics({
    task,
    pack: atomicPack,
    result,
    canonicalContract: makeCanonicalContract(),
  });

  assert.equal(report.valid, false);
  assert(report.errors.some((item) => item.includes("atomic_fact_primary_high_grade_conflict")));
});

test("P0-02: canonical task identity, family and case cardinality fail closed", () => {
  const duplicate = makeTask();
  duplicate.cases.push({ ...duplicate.cases[0] });
  duplicate.ordinal_tasks.push({ ...duplicate.ordinal_tasks[0] });
  duplicate.hard_gate_tasks.push({ ...duplicate.hard_gate_tasks[0] });
  duplicate.state_tasks.push({ ...duplicate.state_tasks[0] });
  const duplicateErrors = validateTaskContract(duplicate, makeCanonicalContract());
  assert(duplicateErrors.includes("task:duplicate_case_id:C1"));
  assert(duplicateErrors.includes("task:duplicate_ordinal_item_id:O1"));
  assert(duplicateErrors.includes("task:duplicate_hard_gate_item_id:G1"));
  assert(duplicateErrors.includes("task:duplicate_state_item_id:S1"));

  const missingFamily = makeTask();
  delete missingFamily.semantic_contract.score_families.earnings;
  const familyErrors = validateTaskContract(missingFamily, makeCanonicalContract());
  assert(familyErrors.includes("semantic_contract:missing_family:earnings"));

  const orphan = makeTask();
  orphan.ordinal_tasks[0].case_id = "NOT-A-CASE";
  const orphanErrors = validateTaskContract(orphan, makeCanonicalContract());
  assert(orphanErrors.includes("O1:unknown_case:NOT-A-CASE"));
});

test("P0-02: submission result cardinality must exactly match the task", () => {
  const task = makeTask();
  const result = makeResult(task);
  result.ordinal = result.ordinal.slice(1);
  result.hard_gate = result.hard_gate.slice(1);
  result.state = [];
  const report = validateSubmissionSemantics({
    task,
    pack,
    result,
    canonicalContract: makeCanonicalContract(),
  });

  assert(report.errors.includes("result:missing_ordinal_item:O1"));
  assert(report.errors.includes("result:missing_hard_gate_item:G1"));
  assert(report.errors.includes("result:missing_state_item:S1"));
});

test("ED-01: revenue production不能支持会计收入类维度", () => {
  const task = makeTask();
  const result = makeResult(task);
  const revenue = result.ordinal.find((item) => item.item_id === "O1");
  revenue.primary_evidence_ids = ["E-PROD"];

  const report = validateSubmissionSemantics({ task, pack, result });
  assert.equal(report.valid, false);
  assert(report.errors.includes("O1:missing_supported_evidence_capability:accounting_revenue_confirmed"));
});

test("P0-02: a false capability and condition tag cannot override unrelated source text", () => {
  const task = makeTask();
  const result = makeResult(task);
  const poisonedPack = structuredClone(pack);
  const generic = poisonedPack.evidence.find((item) => item.evidence_id === "E-GENERAL");
  generic.claim_capabilities.push("accounting_revenue_confirmed");
  generic.condition_ids.push("capability.accounting_revenue_confirmed.v1.4");
  result.ordinal[0] = supported("O1", ["E-GENERAL"]);

  const report = validateSubmissionSemantics({
    task,
    pack: poisonedPack,
    result,
    canonicalContract: makeCanonicalContract(),
  });
  assert.equal(report.valid, false);
  assert(report.errors.includes("O1:missing_supported_evidence_capability:accounting_revenue_confirmed"));
});

test("P0-02: producer self-reported verification cannot replace the independent ledger", () => {
  const task = makeTask();
  const result = makeResult(task);

  const report = validateSubmissionSemanticsRaw({
    task,
    pack,
    result,
    canonicalContract: makeCanonicalContract(),
    verificationLedger: [],
  });

  assert.equal(report.valid, false);
  assert(report.errors.includes("O1:missing_supported_evidence_capability:accounting_revenue_confirmed"));
});

test("P0-02: fabricated flat ledger IDs and an unbound raw hash cannot authorize", () => {
  const revenue = pack.evidence.find((item) => item.evidence_id === "E-REV");
  const assertion = revenue.business_fact_assertions.find(
    (item) => item.canonical_predicate_id
      === "capability.accounting_revenue_confirmed.v1.4",
  );
  const fabricatedFlatLedger = [{
    assertion_id: assertion.assertion_id,
    assertion_sha256: assertionSha256(assertion),
    request_record_id: "caller-invented-request",
    response_record_id: "caller-invented-response",
    result_record_id: "caller-invented-result",
    reconciliation_receipt_id: "caller-invented-reconciliation",
    producer_execution_id: "producer-string",
    verifier_execution_id: "different-verifier-string",
    raw_response_sha256: "b".repeat(64),
    governance_bundle_sha256: TEST_GOVERNANCE_BUNDLE_SHA256,
    decision: "verified",
  }];

  assert.equal(evidenceAuthorizesBusinessCapability({
    evidence: revenue,
    capability: "accounting_revenue_confirmed",
    verificationLedger: fabricatedFlatLedger,
    governanceBundleSha256: TEST_GOVERNANCE_BUNDLE_SHA256,
  }), false);
});

test("P0-02: repository-loaded authority authorizes the bound accounting fact", () => {
  const revenue = pack.evidence.find((item) => item.evidence_id === "E-REV");
  assert.equal(evidenceAuthorizesBusinessCapability({
    evidence: revenue,
    capability: "accounting_revenue_confirmed",
    verificationAuthority: defaultVerificationAuthority,
  }), true);
});

test("C1-P0-002: repository labels without durable provider execution origin cannot authorize", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-forged-authority-"));
  const repositoryPath = writeVerificationRepository(
    path.join(fixtureDir, "theme.db"),
    makeVerificationLedger(pack),
    "node-semantic-test-run",
    { includeProviderExecutionOrigins: false },
  );

  assert.throws(() => loadRepositoryVerificationAuthority({
    repositoryPath,
    runId: "node-semantic-test-run",
    evidencePackId: pack.evidence_pack_id,
    runtimeGovernanceBundlePath: path.join(
      semanticToolDir, "runtime-governance-bundle-v1.json",
    ),
    expectedRuntimeGovernanceBundleId: "theme-chokepoint-evidence-trust-runtime-v1",
    expectedRuntimeGovernanceBundleSha256: TEST_GOVERNANCE_BUNDLE_SHA256,
  }), /durable provider execution origin/i);
});

test("P1-004: repository authority rejects tampered persisted source bytes", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-source-authority-"));
  const repositoryPath = writeVerificationRepository(
    path.join(fixtureDir, "theme.db"),
    makeVerificationLedger(pack),
  );
  const database = new DatabaseSync(repositoryPath);
  try {
    database.prepare(`UPDATE theme_chokepoint_source_versions
      SET raw_bytes = ? WHERE source_version_id = (
        SELECT source_version_id FROM theme_chokepoint_source_versions LIMIT 1
      )`).run(Buffer.from("tampered persisted source bytes"));
  } finally {
    database.close();
  }

  assert.throws(() => loadRepositoryVerificationAuthority({
    repositoryPath,
    runId: "node-semantic-test-run",
    evidencePackId: pack.evidence_pack_id,
    runtimeGovernanceBundlePath: path.join(
      semanticToolDir, "runtime-governance-bundle-v1.json",
    ),
    expectedRuntimeGovernanceBundleId: "theme-chokepoint-evidence-trust-runtime-v1",
    expectedRuntimeGovernanceBundleSha256: TEST_GOVERNANCE_BUNDLE_SHA256,
  }), /source (?:identity|version|context|lineage)/i);
});

test("P1-004: an official-source substring cannot spoof accounting authority", () => {
  const revenue = structuredClone(
    pack.evidence.find((item) => item.evidence_id === "E-REV"),
  );
  revenue.source_type = "not_a_company_filing";
  assert.equal(evidenceAuthorizesBusinessCapability({
    evidence: revenue,
    capability: "accounting_revenue_confirmed",
    verificationAuthority: defaultVerificationAuthority,
  }), false);
});

for (const [name, mutate] of [
  ["negative", (assertion) => { assertion.polarity = "negative"; }],
  ["unknown", (assertion) => { assertion.polarity = "unknown"; }],
  ["conflicted", (assertion) => { assertion.polarity = "conflicted"; }],
  ["pending", (assertion) => { assertion.lifecycle_state = "pending_qualification"; }],
  ["planned", (assertion) => { assertion.lifecycle_state = "planned"; }],
  ["historical", (assertion) => { assertion.lifecycle_state = "historical_ended"; }],
  ["withdrawn", (assertion) => { assertion.lifecycle_state = "withdrawn"; }],
]) {
  test(`P0-02 structured ${name} assertion cannot authorize accounting revenue`, () => {
    const task = makeTask();
    const result = makeResult(task);
    const poisonedPack = structuredClone(pack);
    const revenue = poisonedPack.evidence.find((item) => item.evidence_id === "E-REV");
    mutate(revenue.business_fact_assertions.find(
      (item) => item.canonical_predicate_id === "capability.accounting_revenue_confirmed.v1.4",
    ));

    const report = validateSubmissionSemantics({
      task,
      pack: poisonedPack,
      result,
      canonicalContract: makeCanonicalContract(),
    });
    assert.equal(report.valid, false);
    assert(report.errors.includes("O1:missing_supported_evidence_capability:accounting_revenue_confirmed"));
  });
}

test("P0-02: rejected independent verification cannot authorize accounting revenue", () => {
  const task = makeTask();
  const result = makeResult(task);
  const rejectedLedger = structuredClone(verificationLedger);
  for (const record of rejectedLedger.results) record.decision = "rejected";

  const report = validateSubmissionSemanticsRaw({
    task,
    pack,
    result,
    canonicalContract: makeCanonicalContract(),
    verificationLedger: rejectedLedger,
  });

  assert.equal(report.valid, false);
  assert(report.errors.includes("O1:missing_supported_evidence_capability:accounting_revenue_confirmed"));
});

test("P0-02: verification from another governance bundle cannot authorize", () => {
  const task = makeTask();
  const result = makeResult(task);
  const wrongBundleLedger = makeVerificationLedger(pack, "c".repeat(64));

  const report = validateSubmissionSemanticsRaw({
    task,
    pack,
    result,
    canonicalContract: makeCanonicalContract(),
    verificationLedger: wrongBundleLedger,
    governanceBundleSha256: TEST_GOVERNANCE_BUNDLE_SHA256,
  });

  assert.equal(report.valid, false);
  assert(report.errors.includes("O1:missing_supported_evidence_capability:accounting_revenue_confirmed"));
});

for (const [name, mutate] of [
  ["negated accounting text", (item) => { item.exact_quote = "powerco ups-x accounting revenue is not disclosed"; }],
  ["wrong accounting subject", (item) => { item.exact_quote = "otherco other-product accounting revenue confirmed at $25 million"; }],
  ["non-official accounting source", (item) => { item.source_type = "industry_blog"; }],
]) {
  test(`P0-02: ${name} cannot authorize accounting revenue`, () => {
    const task = makeTask();
    const result = makeResult(task);
    const poisonedPack = structuredClone(pack);
    const revenue = poisonedPack.evidence.find((item) => item.evidence_id === "E-REV");
    revenue.subject_company_id = "powerco";
    revenue.subject_product_id = "ups-x";
    revenue.source_type = "company_filing";
    revenue.exact_quote = "powerco ups-x accounting revenue confirmed at $25 million; multi period revenue";
    mutate(revenue);

    const report = validateSubmissionSemantics({
      task,
      pack: poisonedPack,
      result,
      canonicalContract: makeCanonicalContract(),
    });
    assert.equal(report.valid, false);
    assert(report.errors.includes("O1:missing_supported_evidence_capability:accounting_revenue_confirmed"));
  });
}

test("P0-02: missing capability predicate cannot authorize accounting revenue", () => {
  const task = makeTask();
  const result = makeResult(task);
  const missingPredicatePack = structuredClone(pack);
  const revenue = missingPredicatePack.evidence.find((item) => item.evidence_id === "E-REV");
  revenue.condition_ids = revenue.condition_ids.filter(
    (item) => item !== "capability.accounting_revenue_confirmed.v1.4",
  );

  const report = validateSubmissionSemantics({
    task,
    pack: missingPredicatePack,
    result,
    canonicalContract: makeCanonicalContract(),
  });
  assert.equal(report.valid, false);
  assert(report.errors.includes("O1:missing_supported_evidence_capability:accounting_revenue_confirmed"));
});

test("P0-02: contradicting evidence cannot authorize a supported ordinal", () => {
  const task = makeTask();
  const result = makeResult(task);
  const contradictedPack = structuredClone(pack);
  contradictedPack.evidence.find((item) => item.evidence_id === "E-REV").stance = "contradicts";

  const report = validateSubmissionSemantics({
    task,
    pack: contradictedPack,
    result,
    canonicalContract: makeCanonicalContract(),
  });
  assert.equal(report.valid, false);
  assert(report.errors.includes("O1:supported_evidence_must_have_supports_stance:E-REV"));
});

test("TV-01: Hard Gate和State必须与Ordinal机械重算一致", () => {
  const task = makeTask();
  const result = makeResult(task);
  result.ordinal[0] = {
    ...result.ordinal[0],
    evidence_state: "unknown",
    bound_type: "none",
    rating_min: 0,
    rating_max: 4,
    bound_basis: {
      floor_anchor: null,
      ceiling_anchor: null,
      exact_basis: null,
      unresolved_higher_anchors: [],
      excluded_higher_anchors: []
    },
    withheld_reason: "insufficient_evidence",
  };

  const report = validateSubmissionSemantics({ task, pack, result });
  assert.equal(report.valid, false);
  assert(report.errors.includes("G1:semantic_label_mismatch:expected_unknown:actual_pass"));
  assert(report.errors.includes("S1:semantic_state_mismatch:expected_withheld:actual_material_earnings_path"));
  assert(report.errors.some((error) => error.startsWith("S1:derived_metric_mismatch:decision_coverage:")));
});

test("完整任务、证据能力、Gate与State一致时通过", () => {
  const task = makeTask();
  const result = makeResult(task);

  assert.deepEqual(validateTaskContract(task), []);
  const report = validateSubmissionSemantics({ task, pack, result });
  assert.equal(report.valid, true, report.errors.join("\n"));
  assert.deepEqual(report.errors, []);
});

test("通用有序状态规则可由分数、Coverage、Ordinal和Hard Gate机械求值", () => {
  const task = makeTask();
  task.semantic_contract.score_families.earnings.state_policy = {
    type: "ordered_rules_v1.3",
    eligibility: {
      op: "and",
      args: [
        { op: "metric_gte", metric: "decision_coverage", value: 0.65 },
        { op: "ordinal_resolved", dimension: "revenue_materiality" },
        { op: "gate_is", gate: "earnings_revenue_non_unknown", label: "pass" },
      ],
    },
    rules: [
      {
        state: "material_earnings_path",
        when: {
          op: "and",
          args: [
            { op: "metric_gte", metric: "score_min", value: 65 },
            { op: "ordinal_min", dimension: "margin_transmission", min: 2 },
          ],
        },
      },
      { state: "moderate_earnings_path", when: { op: "constant", value: true } },
    ],
  };
  const result = makeResult(task);

  const report = validateSubmissionSemantics({ task, pack, result });
  assert.equal(report.valid, true, report.errors.join("\n"));
  assert.equal(report.derived.S1.primary_state, "material_earnings_path");
});

test("TV-09: state context is read from the fixed Evidence Pack rather than the annotator result", () => {
  const task = makeTask();
  task.semantic_contract.score_families.earnings.state_policy = {
    type: "ordered_rules_v1.3",
    eligibility: {
      op: "and",
      args: [
        { op: "metric_gte", metric: "decision_coverage", value: 0.65 },
        { op: "context_is", field: "counter_evidence_search_complete", value: true },
      ],
    },
    rules: [{ state: "material_earnings_path", when: { op: "constant", value: true } }],
  };
  const evidencePack = {
    ...pack,
    state_context: {
      C1: { counter_evidence_search_complete: true },
    },
  };

  const report = validateSubmissionSemantics({ task, pack: evidencePack, result: makeResult(task) });
  assert.equal(report.valid, true, report.errors.join("\n"));
  assert.equal(report.derived.S1.primary_state, "material_earnings_path");
});

test("任务合同拒绝未知State policy和未声明Gate引用", () => {
  const task = makeTask();
  task.semantic_contract.score_families.earnings.state_policy = {
    type: "ordered_rules_v1.3",
    eligibility: { op: "gate_is", gate: "not_declared", label: "pass" },
    rules: [{ state: "material_earnings_path", when: { op: "constant", value: true } }],
  };
  let errors = validateTaskContract(task);
  assert(errors.includes("S1:state_expression_unknown_gate:not_declared"));

  task.semantic_contract.score_families.earnings.state_policy = { type: "free_text_policy" };
  errors = validateTaskContract(task);
  assert(errors.includes("S1:unsupported_state_policy:free_text_policy"));
});

test("TD-01: task内嵌权重或Revenue能力要求不得偏离canonical机器合同", () => {
  const task = makeTask();
  task.semantic_contract.score_families.earnings.dimensions.revenue_materiality.weight = 21;
  task.semantic_contract.score_families.earnings.dimensions.revenue_materiality.supported_evidence_any_of = [];

  const errors = validateTaskContract(task, makeCanonicalContract());
  assert(errors.includes("semantic_contract:weight_mismatch:earnings:revenue_materiality:expected_20:actual_21"));
  assert(errors.includes("semantic_contract:evidence_requirement_mismatch:earnings:revenue_materiality"));
});

test("TV-02: task不能改写canonical State policy或发明状态", () => {
  const task = makeTask();
  task.semantic_contract.score_families.earnings.state_policy = {
    type: "ordered_rules_v1.3",
    eligibility: { op: "constant", value: true },
    rules: [{ state: "invented_high_state", when: { op: "constant", value: true } }],
  };

  const errors = validateTaskContract(task, makeCanonicalContract());
  assert(errors.includes("semantic_contract:state_policy_mismatch:earnings"));
});

test("TV-04: task不能改写canonical Hard Gate谓词或阈值", () => {
  const task = makeTask();
  task.hard_gate_tasks[1].predicate.min = 0;

  const errors = validateTaskContract(task, makeCanonicalContract());
  assert(errors.includes("semantic_contract:hard_gate_predicate_mismatch:earnings_margin_min_2"));
});

test("TV-07: state task cannot omit a canonical Hard Gate", () => {
  const task = makeTask();
  task.hard_gate_tasks = task.hard_gate_tasks.filter((item) => item.gate !== "earnings_margin_min_2");
  task.state_tasks[0].hard_gate_item_ids = ["G1"];

  const errors = validateTaskContract(task, makeCanonicalContract());
  assert(errors.includes("S1:missing_required_hard_gate:earnings_margin_min_2"));
});

test("canonical机器合同的每个加权评分族必须合计100", () => {
  const canonical = makeCanonicalContract();
  canonical.score_families.earnings.weighted_dimensions.revenue_materiality = 19;

  const errors = validateTaskContract(makeTask(), canonical);
  assert(errors.includes("semantic_contract:canonical_weight_sum:earnings:expected_100:actual_99"));
});

test("CLI对固定task、pack和result输出可封存JSON报告", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-chokepoint-v13-"));
  const taskPath = path.join(fixtureDir, "task.json");
  const packPath = path.join(fixtureDir, "pack.json");
  const resultPath = path.join(fixtureDir, "result.json");
  const semanticContractPath = path.join(fixtureDir, "semantic-contract.json");
  fs.writeFileSync(taskPath, `${JSON.stringify(makeTask(), null, 2)}\n`);
  fs.writeFileSync(packPath, `${JSON.stringify(pack, null, 2)}\n`);
  fs.writeFileSync(resultPath, `${JSON.stringify(makeResult(makeTask()), null, 2)}\n`);
  fs.writeFileSync(semanticContractPath, `${JSON.stringify(makeCanonicalContract(), null, 2)}\n`);
  const governance = writeRuntimeGovernanceBundle(fixtureDir, semanticContractPath);
  const verificationRepositoryPath = writeVerificationRepository(
    path.join(fixtureDir, "theme.db"),
    makeVerificationLedger(pack, governance.bundleSha256, governance.bundleId),
    "cli-semantic-test-run",
  );

  const here = path.dirname(fileURLToPath(import.meta.url));
  const run = spawnSync(process.execPath, [
    path.join(here, "validate-submission-v1.4.mjs"),
    "--task", taskPath,
    "--pack", packPath,
    "--result", resultPath,
    "--semantic-contract", semanticContractPath,
    "--verification-repository", verificationRepositoryPath,
    "--verification-run-id", "cli-semantic-test-run",
    "--runtime-governance-bundle", governance.bundlePath,
    "--expected-runtime-governance-bundle-id", governance.bundleId,
    "--expected-runtime-governance-bundle-sha256", governance.bundleSha256,
    "--expected-semantic-contract-sha256", fileSha256(semanticContractPath),
    "--allow-unfrozen-overlay",
  ], { encoding: "utf8" });

  assert.equal(run.status, 0, run.stderr || run.stdout);
  const report = JSON.parse(run.stdout);
  assert.equal(report.valid, true);
  assert.equal(report.semantic_contract_version, "theme-chokepoint-semantic-task-contract-v1.4");
  assert.equal(report.sha256.semantic_contract.length, 64);
  assert.equal(report.derived.S1.primary_state, "material_earnings_path");
});

test("P0-02: CLI cannot use overlay opt-in without an exact governance bundle", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-chokepoint-v14-governance-"));
  const taskPath = path.join(fixtureDir, "task.json");
  const packPath = path.join(fixtureDir, "pack.json");
  const resultPath = path.join(fixtureDir, "result.json");
  const semanticContractPath = path.join(fixtureDir, "semantic-contract.json");
  const verificationLedgerPath = path.join(fixtureDir, "verification-ledger.json");
  fs.writeFileSync(taskPath, `${JSON.stringify(makeTask(), null, 2)}\n`);
  fs.writeFileSync(packPath, `${JSON.stringify(pack, null, 2)}\n`);
  fs.writeFileSync(resultPath, `${JSON.stringify(makeResult(makeTask()), null, 2)}\n`);
  fs.writeFileSync(semanticContractPath, `${JSON.stringify(makeCanonicalContract(), null, 2)}\n`);
  fs.writeFileSync(verificationLedgerPath, `${JSON.stringify(verificationLedger, null, 2)}\n`);

  const here = path.dirname(fileURLToPath(import.meta.url));
  const run = spawnSync(process.execPath, [
    path.join(here, "validate-submission-v1.4.mjs"),
    "--task", taskPath,
    "--pack", packPath,
    "--result", resultPath,
    "--semantic-contract", semanticContractPath,
    "--verification-ledger", verificationLedgerPath,
    "--expected-semantic-contract-sha256", fileSha256(semanticContractPath),
    "--allow-unfrozen-overlay",
  ], { encoding: "utf8" });

  assert.equal(run.status, 1);
  const report = JSON.parse(run.stdout);
  assert(report.errors.includes("runtime_governance_bundle:required"));
});

test("P0-02: CLI rejects a caller-supplied ledger without a repository boundary", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-chokepoint-v14-ledger-pin-"));
  const files = {
    task: path.join(fixtureDir, "task.json"),
    pack: path.join(fixtureDir, "pack.json"),
    result: path.join(fixtureDir, "result.json"),
    semantic: path.join(fixtureDir, "semantic-contract.json"),
    ledger: path.join(fixtureDir, "verification-ledger.json"),
  };
  fs.writeFileSync(files.task, `${JSON.stringify(makeTask(), null, 2)}\n`);
  fs.writeFileSync(files.pack, `${JSON.stringify(pack, null, 2)}\n`);
  fs.writeFileSync(files.result, `${JSON.stringify(makeResult(makeTask()), null, 2)}\n`);
  fs.writeFileSync(files.semantic, `${JSON.stringify(makeCanonicalContract(), null, 2)}\n`);
  const governance = writeRuntimeGovernanceBundle(fixtureDir, files.semantic);
  fs.writeFileSync(
    files.ledger,
    `${JSON.stringify(makeVerificationLedger(
      pack, governance.bundleSha256, governance.bundleId,
    ), null, 2)}\n`,
  );

  const here = path.dirname(fileURLToPath(import.meta.url));
  const run = spawnSync(process.execPath, [
    path.join(here, "validate-submission-v1.4.mjs"),
    "--task", files.task,
    "--pack", files.pack,
    "--result", files.result,
    "--semantic-contract", files.semantic,
    "--verification-ledger", files.ledger,
    "--runtime-governance-bundle", governance.bundlePath,
    "--expected-runtime-governance-bundle-id", governance.bundleId,
    "--expected-runtime-governance-bundle-sha256", governance.bundleSha256,
    "--expected-semantic-contract-sha256", fileSha256(files.semantic),
    "--allow-unfrozen-overlay",
  ], { encoding: "utf8" });

  assert.equal(run.status, 1);
  const report = JSON.parse(run.stdout);
  assert(report.errors.includes("verification_repository:required"));
});

test("P0-02: CLI fails closed when a governance child artifact drifts", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-chokepoint-v14-child-drift-"));
  const files = {
    task: path.join(fixtureDir, "task.json"),
    pack: path.join(fixtureDir, "pack.json"),
    result: path.join(fixtureDir, "result.json"),
    semantic: path.join(fixtureDir, "semantic-contract.json"),
    ledger: path.join(fixtureDir, "verification-ledger.json"),
  };
  fs.writeFileSync(files.task, `${JSON.stringify(makeTask(), null, 2)}\n`);
  fs.writeFileSync(files.pack, `${JSON.stringify(pack, null, 2)}\n`);
  fs.writeFileSync(files.result, `${JSON.stringify(makeResult(makeTask()), null, 2)}\n`);
  fs.writeFileSync(files.semantic, `${JSON.stringify(makeCanonicalContract(), null, 2)}\n`);
  fs.writeFileSync(files.ledger, `${JSON.stringify(verificationLedger, null, 2)}\n`);
  const governance = writeRuntimeGovernanceBundle(fixtureDir, files.semantic);
  fs.appendFileSync(path.join(fixtureDir, "business-fact-decision-table-v1.4.json"), " \n");

  const here = path.dirname(fileURLToPath(import.meta.url));
  const run = spawnSync(process.execPath, [
    path.join(here, "validate-submission-v1.4.mjs"),
    "--task", files.task,
    "--pack", files.pack,
    "--result", files.result,
    "--semantic-contract", files.semantic,
    "--verification-ledger", files.ledger,
    "--runtime-governance-bundle", governance.bundlePath,
    "--expected-runtime-governance-bundle-id", governance.bundleId,
    "--expected-runtime-governance-bundle-sha256", governance.bundleSha256,
    "--expected-semantic-contract-sha256", fileSha256(files.semantic),
    "--allow-unfrozen-overlay",
  ], { encoding: "utf8" });

  assert.equal(run.status, 1);
  const report = JSON.parse(run.stdout);
  assert(report.errors.includes(
    "runtime_governance_bundle:business_fact_decision_table:sha256_mismatch",
  ));
});

test("CLI遇到损坏的submission数组时返回JSON错误而不是崩溃", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-chokepoint-v13-invalid-"));
  const files = {
    task: path.join(fixtureDir, "task.json"),
    pack: path.join(fixtureDir, "pack.json"),
    result: path.join(fixtureDir, "result.json"),
    semantic: path.join(fixtureDir, "semantic-contract.json"),
  };
  const invalidResult = makeResult(makeTask());
  invalidResult.ordinal = {};
  fs.writeFileSync(files.task, `${JSON.stringify(makeTask(), null, 2)}\n`);
  fs.writeFileSync(files.pack, `${JSON.stringify(pack, null, 2)}\n`);
  fs.writeFileSync(files.result, `${JSON.stringify(invalidResult, null, 2)}\n`);
  fs.writeFileSync(files.semantic, `${JSON.stringify(makeCanonicalContract(), null, 2)}\n`);

  const here = path.dirname(fileURLToPath(import.meta.url));
  const run = spawnSync(process.execPath, [
    path.join(here, "validate-submission-v1.4.mjs"),
    "--task", files.task,
    "--pack", files.pack,
    "--result", files.result,
    "--semantic-contract", files.semantic,
    "--expected-semantic-contract-sha256", fileSha256(files.semantic),
    "--allow-unfrozen-overlay",
  ], { encoding: "utf8" });

  assert.equal(run.status, 1);
  const report = JSON.parse(run.stdout);
  assert.equal(report.valid, false);
  assert(report.errors.includes("ordinal:not_array"));
});

test("TV-03: CLI拒绝省略task、contract或evidence pack绑定的result", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-chokepoint-v14-binding-"));
  const files = {
    task: path.join(fixtureDir, "task.json"),
    pack: path.join(fixtureDir, "pack.json"),
    result: path.join(fixtureDir, "result.json"),
    semantic: path.join(fixtureDir, "semantic-contract.json"),
  };
  const result = makeResult(makeTask());
  delete result.contract_id;
  fs.writeFileSync(files.task, `${JSON.stringify(makeTask(), null, 2)}\n`);
  fs.writeFileSync(files.pack, `${JSON.stringify(pack, null, 2)}\n`);
  fs.writeFileSync(files.result, `${JSON.stringify(result, null, 2)}\n`);
  fs.writeFileSync(files.semantic, `${JSON.stringify(makeCanonicalContract(), null, 2)}\n`);

  const here = path.dirname(fileURLToPath(import.meta.url));
  const run = spawnSync(process.execPath, [
    path.join(here, "validate-submission-v1.4.mjs"),
    "--task", files.task,
    "--pack", files.pack,
    "--result", files.result,
    "--semantic-contract", files.semantic,
    "--expected-semantic-contract-sha256", fileSha256(files.semantic),
    "--allow-unfrozen-overlay",
  ], { encoding: "utf8" });

  assert.equal(run.status, 1);
  const report = JSON.parse(run.stdout);
  assert(report.errors.includes("result_contract_binding_required"));
});

test("EX-01: CLI rejects an unpinned executable semantic contract", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-chokepoint-v14-unpinned-"));
  const files = {
    task: path.join(fixtureDir, "task.json"),
    pack: path.join(fixtureDir, "pack.json"),
    result: path.join(fixtureDir, "result.json"),
    semantic: path.join(fixtureDir, "semantic-contract.json"),
  };
  fs.writeFileSync(files.task, `${JSON.stringify(makeTask(), null, 2)}\n`);
  fs.writeFileSync(files.pack, `${JSON.stringify(pack, null, 2)}\n`);
  fs.writeFileSync(files.result, `${JSON.stringify(makeResult(makeTask()), null, 2)}\n`);
  fs.writeFileSync(files.semantic, `${JSON.stringify(makeCanonicalContract(), null, 2)}\n`);

  const here = path.dirname(fileURLToPath(import.meta.url));
  const run = spawnSync(process.execPath, [
    path.join(here, "validate-submission-v1.4.mjs"),
    "--task", files.task,
    "--pack", files.pack,
    "--result", files.result,
    "--semantic-contract", files.semantic,
  ], { encoding: "utf8" });

  assert.equal(run.status, 1);
  const report = JSON.parse(run.stdout);
  assert(report.errors.includes("semantic_contract:expected_sha256_required"));
});

test("EX-02: CLI rejects byte mutation against the caller-pinned SHA", () => {
  const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), "theme-chokepoint-v14-sha-"));
  const files = {
    task: path.join(fixtureDir, "task.json"),
    pack: path.join(fixtureDir, "pack.json"),
    result: path.join(fixtureDir, "result.json"),
    semantic: path.join(fixtureDir, "semantic-contract.json"),
  };
  fs.writeFileSync(files.task, `${JSON.stringify(makeTask(), null, 2)}\n`);
  fs.writeFileSync(files.pack, `${JSON.stringify(pack, null, 2)}\n`);
  fs.writeFileSync(files.result, `${JSON.stringify(makeResult(makeTask()), null, 2)}\n`);
  fs.writeFileSync(files.semantic, `${JSON.stringify(makeCanonicalContract(), null, 2)}\n`);
  const expectedSha = fileSha256(files.semantic);
  fs.appendFileSync(files.semantic, " \n");

  const here = path.dirname(fileURLToPath(import.meta.url));
  const run = spawnSync(process.execPath, [
    path.join(here, "validate-submission-v1.4.mjs"),
    "--task", files.task,
    "--pack", files.pack,
    "--result", files.result,
    "--semantic-contract", files.semantic,
    "--expected-semantic-contract-sha256", expectedSha,
    "--allow-unfrozen-overlay",
  ], { encoding: "utf8" });

  assert.equal(run.status, 1);
  const report = JSON.parse(run.stdout);
  assert(report.errors.some((error) => error.startsWith("semantic_contract:sha256_mismatch:")));
});

test("AF-05-01: exact 3 without a supported ceiling is rejected", () => {
  const task = makeTask();
  const result = makeResult(task);
  result.ordinal[0] = {
    ...result.ordinal[0],
    bound_type: "exact",
    rating_min: 3,
    rating_max: 3,
    bound_basis: {
      floor_anchor: 3,
      ceiling_anchor: null,
      exact_basis: null,
      unresolved_higher_anchors: [4],
      excluded_higher_anchors: []
    }
  };

  const report = validateSubmissionSemantics({ task, pack, result });
  assert.equal(report.valid, false);
  assert(report.errors.includes("O1:exact_requires_supported_ceiling"));
});

test("AF-05-01: lower_bound 3 records unresolved higher anchors without inventing a ceiling", () => {
  const task = makeTask();
  const result = makeResult(task);
  result.ordinal[0] = {
    ...result.ordinal[0],
    bound_type: "lower_bound",
    rating_min: 3,
    rating_max: 4,
    bound_basis: {
      floor_anchor: 3,
      ceiling_anchor: null,
      exact_basis: null,
      unresolved_higher_anchors: [4],
      excluded_higher_anchors: []
    }
  };

  const report = validateSubmissionSemantics({ task, pack, result });
  assert(!report.errors.some((error) => error.startsWith("O1:bound_basis")), report.errors.join("\n"));
  assert(!report.errors.includes("O1:exact_requires_supported_ceiling"));
});

test("EV-09: supported lower bound without decisive Evidence and Claim binding is rejected", () => {
  const task = makeTask();
  const result = makeResult(task);
  result.ordinal[1] = {
    ...result.ordinal[1],
    bound_type: "lower_bound",
    rating_min: 3,
    rating_max: 4,
    primary_evidence_ids: [],
    bound_basis: {
      floor_anchor: 3,
      ceiling_anchor: null,
      exact_basis: null,
      unresolved_higher_anchors: [4],
      excluded_higher_anchors: []
    },
    anchor_conditions: []
  };

  const report = validateSubmissionSemantics({ task, pack: { ...pack, evidence: [] }, result });
  assert.equal(report.valid, false);
  assert(report.errors.includes("O2:supported_primary_evidence_required"));
  assert(report.errors.includes("O2:lower_bound_requires_decisive_floor_evidence"));
});

test("TV-05: structurally valid exact 3 still requires decisive floor and ceiling evidence", () => {
  const task = makeTask();
  const result = makeResult(task);
  result.ordinal[0] = {
    ...result.ordinal[0],
    bound_type: "exact",
    rating_min: 3,
    rating_max: 3,
    bound_basis: {
      floor_anchor: 3,
      ceiling_anchor: 3,
      exact_basis: "direct_upper_bound",
      unresolved_higher_anchors: [],
      excluded_higher_anchors: [4]
    },
    anchor_conditions: []
  };

  const report = validateSubmissionSemantics({ task, pack, result });
  assert.equal(report.valid, false);
  assert(report.errors.includes("O1:exact_requires_decisive_floor_evidence"));
  assert(report.errors.includes("O1:exact_requires_decisive_ceiling_evidence"));
});

test("TV-08: exact conditions must use the canonical predicate directory IDs", () => {
  const task = makeTask();
  const result = makeResult(task);
  result.ordinal[0] = {
    ...result.ordinal[0],
    rating_min: 3,
    rating_max: 3,
    bound_basis: {
      floor_anchor: 3,
      ceiling_anchor: 3,
      exact_basis: "direct_upper_bound",
      unresolved_higher_anchors: [],
      excluded_higher_anchors: [4]
    },
    anchor_conditions: [
      {
        condition_id: "invented.floor",
        condition_type: "floor",
        anchor: 3,
        evidence_state: "supported",
        evidence_ids: ["E-GENERAL"],
        decisive_claim_ids: ["claim-general"],
        excluded_higher_anchors: []
      },
      {
        condition_id: "invented.ceiling",
        condition_type: "ceiling",
        anchor: 3,
        evidence_state: "supported",
        evidence_ids: ["E-GENERAL"],
        decisive_claim_ids: ["claim-general"],
        excluded_higher_anchors: [4]
      }
    ]
  };
  const evidencePack = {
    ...pack,
    evidence: pack.evidence.map((item) => (
      item.evidence_id === "E-GENERAL"
        ? { ...item, claim_ids: ["claim-general"] }
        : item
    )),
  };

  const report = validateSubmissionSemantics({ task, pack: evidencePack, result });
  assert(report.errors.includes("O1:noncanonical_floor_condition_id"));
  assert(report.errors.includes("O1:noncanonical_ceiling_condition_id"));
});

function makeReplacementModeTask(mode, anchorProfile) {
  return {
    task_id: "replacement-mode-fixture-v1.4",
    contract_id: "theme-chokepoint-scoring-v1.4",
    evidence_pack_id: "replacement-mode-pack-v1.4",
    cases: [{ case_id: "R1", assessment_track: "replacement", replacement_mode: mode }],
    semantic_contract: {
      version: "theme-chokepoint-semantic-task-contract-v1.4",
      score_families: {},
      replacement_anchor_profiles: {
        product: { performance_parity: "replacement.product.performance_parity.v1.4" },
        service: { performance_parity: "replacement.service.performance_parity.v1.4" }
      }
    },
    ordinal_tasks: [{
      item_id: "OR1",
      case_id: "R1",
      score_family: "replacement",
      dimension: "performance_parity",
      anchor_profile: anchorProfile
    }],
    hard_gate_tasks: [],
    state_tasks: []
  };
}

function makeReplacementModeCanonical() {
  return {
    contract_id: "theme-chokepoint-semantic-task-contract-v1.4",
    score_families: {},
    replacement_anchor_profiles: {
      product: { performance_parity: "replacement.product.performance_parity.v1.4" },
      service: { performance_parity: "replacement.service.performance_parity.v1.4" }
    }
  };
}

test("CF-05-02: every replacement case declares product or service mode", () => {
  const task = makeReplacementModeTask(undefined, "replacement.product.performance_parity.v1.4");
  const errors = validateTaskContract(task, makeReplacementModeCanonical());
  assert(errors.includes("R1:replacement_mode_required"));
});

test("CF-05-02: service replacement cannot use the product anchor profile", () => {
  const task = makeReplacementModeTask("service", "replacement.product.performance_parity.v1.4");
  const errors = validateTaskContract(task, makeReplacementModeCanonical());
  assert(errors.includes("OR1:anchor_profile_mismatch:expected_replacement.service.performance_parity.v1.4:actual_replacement.product.performance_parity.v1.4"));
});

test("CF-05-02: matching service profile does not bypass incomplete task rejection", () => {
  const task = makeReplacementModeTask("service", "replacement.service.performance_parity.v1.4");
  const errors = validateTaskContract(task, makeReplacementModeCanonical());
  assert.equal(errors.some((error) => error.includes("anchor_profile_mismatch")), false);
  assert(errors.includes("task:hard_gate_task_required"));
  assert(errors.includes("task:state_task_required"));
});

test("CF-05-01: v1.4 earnings family uses split concentration and protection dimensions", () => {
  const task = makeTask();
  const dimensionsByName = task.semantic_contract.score_families.earnings.dimensions;
  assert.equal(dimensionsByName.customer_concentration_exposure.weight, 2.5);
  assert.equal(dimensionsByName.customer_relationship_protection.weight, 2.5);
  assert.equal(Object.hasOwn(dimensionsByName, "customer_concentration_risk"), false);
  assert.deepEqual(validateTaskContract(task, makeCanonicalContract()), []);
});

test("AF-05-01: every Ordinal result requires bound_basis", () => {
  const task = makeTask();
  const result = makeResult(task);
  delete result.ordinal[0].bound_basis;
  const report = validateSubmissionSemantics({ task, pack, result });
  assert(report.errors.includes("O1:bound_basis_required"));
});

test("canonical v1.4 keeps 100-point families and complete product/service profiles", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const canonical = JSON.parse(fs.readFileSync(path.join(here, "semantic-task-contract-v1.4.json"), "utf8"));
  const earnings = canonical.score_families.earnings.weighted_dimensions;
  assert.equal(Object.values(earnings).reduce((sum, weight) => sum + weight, 0), 100);
  assert.equal(earnings.customer_concentration_exposure, 2.5);
  assert.equal(earnings.customer_relationship_protection, 2.5);
  assert.equal(Object.hasOwn(earnings, "customer_concentration_risk"), false);
  assert.equal(Object.keys(canonical.replacement_anchor_profiles.product).length, 11);
  assert.equal(Object.keys(canonical.replacement_anchor_profiles.service).length, 11);
  for (const [family, definition] of Object.entries(canonical.score_families)) {
    const expected = [
      ...Object.keys(definition.weighted_dimensions ?? {}),
      ...(definition.non_weighted_dimensions ?? []),
    ];
    assert.deepEqual(
      canonical.anchor_predicate_directory.dimensions_by_family[family],
      expected,
      `${family} anchor predicate directory drift`,
    );
  }
});

test("TV-06: canonical v1.4 freezes every score-family state priority and Hard Gate predicate", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const canonical = JSON.parse(fs.readFileSync(path.join(here, "semantic-task-contract-v1.4.json"), "utf8"));
  const expectedStates = {
    segment: ["not_supported", "strong_candidate_chokepoint", "candidate_chokepoint", "watch_segment", "not_supported"],
    defensibility: ["high_defensibility", "low_defensibility", "medium_defensibility"],
    replacement: ["failed_or_withdrawn", "scaled_replacement", "realized_replacement", "scaled_alternative", "production_alternative", "replacement_ready", "credible_challenge", "early_signal"],
    earnings: ["weak_earnings_capture", "material_earnings_path", "moderate_earnings_path"],
  };
  for (const [family, states] of Object.entries(expectedStates)) {
    const policy = canonical.score_families[family].state_policy;
    assert.equal(policy.type, "ordered_rules_v1.3", `${family} missing canonical ordered policy`);
    assert.deepEqual(policy.rules.map((rule) => rule.state), states, `${family} priority drift`);
  }
  for (const gate of [
    "failed_or_withdrawn_gate",
    "replacement_discovery_gate",
    "performance_min_2",
    "qualification_min_3",
    "capacity_min_2",
    "ecosystem_min_2",
    "fatal_blocker_clear",
    "production_use_gate",
    "qualified_saleable_output_gate",
    "scale_gate",
    "displacement_min_3",
    "earnings_revenue_resolved",
    "earnings_margin_resolved",
    "earnings_time_to_revenue_resolved",
    "earnings_mandatory_min_2",
  ]) {
    assert(canonical.hard_gate_predicates[gate], `missing canonical gate ${gate}`);
  }

  const segmentPolicy = JSON.stringify(canonical.score_families.segment.state_policy);
  for (const field of [
    "demand_direct_evidence",
    "supply_direct_evidence",
    "counter_evidence_search_complete",
    "mandatory_conflict",
    "independent_supply_constraint_count",
    "key_source_quality_high",
  ]) {
    assert(segmentPolicy.includes(`\"field\":\"${field}\"`), `segment policy missing context ${field}`);
  }

  const replacementRules = Object.fromEntries(
    canonical.score_families.replacement.state_policy.rules.map((rule) => [rule.state, JSON.stringify(rule.when)]),
  );
  for (const state of ["production_alternative", "scaled_alternative", "realized_replacement", "scaled_replacement"]) {
    assert(replacementRules[state].includes("production_use_gate"), `${state} does not inherit Production use`);
    assert(replacementRules[state].includes("qualified_saleable_output_gate"), `${state} lacks qualified saleable output`);
    assert(replacementRules[state].includes("score_min"), `${state} does not inherit Ready score`);
    assert(replacementRules[state].includes("decision_coverage"), `${state} does not inherit Ready coverage`);
  }
});
