import { z } from "zod";

import { failure, type Failure, type StableErrorCode } from "./errors.js";

export const KNOWN_PYTHON_STATUSES = [
  "REQUEST_STORED",
  "NEEDS_CLARIFICATION",
  "AWAITING_PRODUCT_CONFIRMATION",
  "READY_FOR_SUPPLY_CHAIN",
  "SUPPLY_CHAIN_GRAPH_READY",
  "CHOKEPOINT_ASSESSMENT_READY",
  "INCOMPLETE_BUDGET_EXHAUSTED",
  "COMPANY_ASSESSMENT_INCOMPLETE",
  "COMPANY_ASSESSMENT_READY",
  "PERSISTENT_RESEARCH_READY",
  "MONITORING_READY",
  "SIGNAL_EXPORT_READY",
] as const;

export type PythonStatus = (typeof KNOWN_PYTHON_STATUSES)[number];

const runDataSchema = z
  .object({
    schema_version: z.literal("theme-chokepoint-mcp.v1"),
    run_id: z.string().min(1),
    status: z.string().min(1),
    next_stage: z.number().int().min(2).max(7).nullable(),
    confirmed_anchor_ids: z.array(z.string().min(1)),
    confirmed_by: z.string().min(1).nullable(),
    confirmed_at: z.string().min(1).nullable(),
  })
  .strict();

const successEnvelope = z
  .object({ ok: z.literal(true), data: runDataSchema })
  .strict();

const stageSchema = z
  .object({
    stage: z.number().int().min(1).max(7),
    input_status: z.string().nullable(),
    output_status: z.string().min(1),
    outcome: z.string(),
    artifact_ids: z.array(z.string()),
    completed_at: z.string().min(1),
  })
  .strict();

export const manifestEnvelopeSchema = z
  .object({
    ok: z.literal(true),
    data: z
      .object({
        run_id: z.string().min(1),
        contract_id: z.string().min(1),
        executable_contract_id: z.string().min(1),
        executable_contract_sha256: z.string().regex(/^[a-f0-9]{64}$/),
        stages: z.array(stageSchema),
        final_status: z.string().min(1),
        created_at: z.string().min(1),
        updated_at: z.string().min(1),
      })
      .strict(),
  })
  .strict();

const anchorSchema = z
  .object({
    anchor_id: z.string().min(1),
    product_name: z.string(),
    buyer_or_user: z.string(),
    demand_variable: z.string(),
    theme_link: z.string(),
    confidence: z.number().finite(),
    supporting_evidence_ids: z.array(z.string()),
    missing_evidence: z.array(z.string()),
    status: z.literal("proposed"),
  })
  .strict();

export const pendingEnvelopeSchema = z
  .object({
    ok: z.literal(true),
    data: z
      .object({
        schema_version: z.literal("theme-chokepoint-mcp.v1"),
        run_id: z.string().min(1),
        status: z.literal("AWAITING_PRODUCT_CONFIRMATION"),
        anchors: z.array(anchorSchema),
      })
      .strict(),
  })
  .strict();

export const confirmEnvelopeSchema = z
  .object({
    ok: z.literal(true),
    data: z
      .object({
        schema_version: z.literal("theme-chokepoint-mcp.v1"),
        run_id: z.string().min(1),
        status: z.literal("READY_FOR_SUPPLY_CHAIN"),
        confirmed_anchor_ids: z.array(z.string().min(1)).min(1),
        confirmed_by: z.string().min(1),
        confirmed_at: z.string().min(1),
      })
      .strict(),
  })
  .strict();

export type Confirmation = {
  runId: string;
  confirmedAnchorIds: string[];
  confirmedBy: string;
  confirmedAt: string;
};

export type DecodedRun = {
  runId: string;
  status: PythonStatus;
  nextStage: number | null;
  confirmation: Confirmation | null;
};

export type RunDecode =
  | { kind: "ok"; data: DecodedRun }
  | { kind: "unknown-status" }
  | { kind: "schema-mismatch" };

const unconfirmed = new Set<PythonStatus>([
  "REQUEST_STORED",
  "NEEDS_CLARIFICATION",
  "AWAITING_PRODUCT_CONFIRMATION",
]);
const known = new Set<string>(KNOWN_PYTHON_STATUSES);

export function decodeRunResponse(
  value: unknown,
  expectedRunId: string,
): RunDecode {
  const parsed = successEnvelope.safeParse(value);
  if (!parsed.success) return { kind: "schema-mismatch" };
  const data = parsed.data.data;
  if (!known.has(data.status)) return { kind: "unknown-status" };
  const status = data.status as PythonStatus;
  if (data.run_id !== expectedRunId) return { kind: "schema-mismatch" };
  const ids = data.confirmed_anchor_ids;
  const idsUnique = ids.length === new Set(ids).size;
  const hasReceipt =
    ids.length > 0 &&
    idsUnique &&
    data.confirmed_by !== null &&
    data.confirmed_by.trim() === data.confirmed_by &&
    data.confirmed_by.length > 0 &&
    data.confirmed_at !== null &&
    /(?:Z|[+-]\d\d:\d\d)$/.test(data.confirmed_at) &&
    Number.isFinite(Date.parse(data.confirmed_at));
  if (unconfirmed.has(status)) {
    if (
      ids.length !== 0 ||
      data.confirmed_by !== null ||
      data.confirmed_at !== null
    ) {
      return { kind: "schema-mismatch" };
    }
  } else if (!hasReceipt) {
    return { kind: "schema-mismatch" };
  }
  return {
    kind: "ok",
    data: {
      runId: data.run_id,
      status,
      nextStage: data.next_stage,
      confirmation: hasReceipt
        ? {
            runId: data.run_id,
            confirmedAnchorIds: [...ids],
            confirmedBy: data.confirmed_by!,
            confirmedAt: data.confirmed_at!,
          }
        : null,
    },
  };
}

const failureEnvelopeSchema = z
  .object({
    ok: z.literal(false),
    error: z
      .object({
        code: z.string(),
        message: z.string(),
        retryable: z.boolean(),
      })
      .strict(),
  })
  .strict();

const knownFailureCodes = new Set<StableErrorCode>([
  "INVALID_ARGUMENT",
  "RUN_ALREADY_EXISTS",
  "RUN_NOT_FOUND",
  "RUN_NOT_AWAITING_CONFIRMATION",
  "ANCHOR_NOT_PROPOSED_FOR_RUN",
  "DUPLICATE_ANCHOR_ID",
  "RUNTIME_NOT_CONFIGURED",
  "RUN_CORRELATION_MISMATCH",
  "PYTHON_STATUS_MISMATCH",
  "MCP_RESPONSE_SCHEMA_MISMATCH",
  "MCP_TOOL_FAILURE",
  "MASTRA_RUN_NOT_FOUND",
  "STORAGE_FAILURE",
  "CONCURRENT_OR_DUPLICATE_RESUME",
  "UNKNOWN_PYTHON_STATUS",
]);

export function decodeFailureResponse(value: unknown): Failure | null {
  const parsed = failureEnvelopeSchema.safeParse(value);
  if (!parsed.success) return null;
  const code = parsed.data.error.code as StableErrorCode;
  if (!knownFailureCodes.has(code)) return failure("MCP_TOOL_FAILURE");
  return failure(
    code,
    code === "MCP_TOOL_FAILURE" && parsed.data.error.retryable,
  );
}
