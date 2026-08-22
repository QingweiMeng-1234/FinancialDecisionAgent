import { randomUUID } from "node:crypto";
import { Mastra } from "@mastra/core";
import { RequestContext } from "@mastra/core/request-context";
import { createStep, createWorkflow } from "@mastra/core/workflows";
import { LibSQLStore } from "@mastra/libsql";
import { MCPClient } from "@mastra/mcp";
import { z } from "zod";

export const WORKFLOW_ID = "theme-chokepoint-m0";
export const SUSPEND_STEP_ID = "await-product-confirmation";
const MCP_SCHEMA_VERSION = "theme-chokepoint-mcp.v1";
const DEFAULT_MCP_URL = "http://127.0.0.1:8877/mcp";

const knownStatusSchema = z.enum([
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
]);
type PythonStatus = z.infer<typeof knownStatusSchema>;

const idSchema = z.string().trim().min(1).max(128);
const isoTimeSchema = z.string().datetime({ offset: true });
const strictStringArray = z.array(z.string().trim().min(1));

export const researchRequestSchema = z.object({
  theme: z.string().trim().min(1),
  trigger: z.string().trim().min(1),
  region: z.string().trim().min(1),
  asOfDate: z.string().date(),
  timeHorizonMonths: z.number().int().positive().finite(),
  analysisGoal: z.string().trim().min(1),
  seedProducts: strictStringArray,
  seedCompanies: strictStringArray,
  researchMode: z.literal("assisted"),
  maxDepth: z.number().int().positive().finite(),
  maxNodes: z.number().int().positive().finite(),
  maxIterations: z.number().int().positive().finite(),
  maxSources: z.number().int().positive().finite(),
  maxTimeSeconds: z.number().int().positive().finite(),
  maxCostUsd: z.number().nonnegative().finite(),
  maxProductAnchors: z.number().int().positive().finite(),
}).strict();
export type ResearchRequestInput = z.input<typeof researchRequestSchema>;

const anchorSchema = z.object({
  anchor_id: idSchema,
  product_name: z.string(),
  buyer_or_user: z.string(),
  demand_variable: z.string(),
  theme_link: z.string(),
  confidence: z.number().finite(),
  supporting_evidence_ids: z.array(z.string()),
  missing_evidence: z.array(z.string()),
  status: z.string(),
}).strict();
type Anchor = z.infer<typeof anchorSchema>;

const stageSchema = z.object({
  stage: z.number().int().min(1).max(7),
  input_status: z.string().nullable(),
  output_status: z.string(),
  outcome: z.string(),
  artifact_ids: z.array(z.string()),
  completed_at: isoTimeSchema,
}).strict();

const errorSchema = z.object({
  code: z.string(),
  message: z.string(),
  retryable: z.boolean(),
}).strict();

const envelope = <T extends z.ZodType>(data: T) => z.union([
  z.object({ ok: z.literal(true), data }).strict(),
  z.object({ ok: z.literal(false), error: errorSchema }).strict(),
]);

const startDataSchema = z.object({
  schema_version: z.literal(MCP_SCHEMA_VERSION),
  run_id: idSchema,
  status: z.string(),
  next_stage: z.number().int().nullable(),
}).strict();
const runDataSchema = z.object({
  schema_version: z.literal(MCP_SCHEMA_VERSION),
  run_id: idSchema,
  status: z.string(),
  next_stage: z.number().int().nullable(),
  confirmed_by: z.string().nullable(),
  confirmed_at: isoTimeSchema.nullable(),
}).strict();
const pendingDataSchema = z.object({
  schema_version: z.literal(MCP_SCHEMA_VERSION),
  run_id: idSchema,
  status: z.string(),
  demand_frame: z.record(z.string(), z.unknown()),
  anchors: z.array(anchorSchema),
}).strict();
const confirmationDataSchema = z.object({
  schema_version: z.literal(MCP_SCHEMA_VERSION),
  run_id: idSchema,
  status: z.string(),
  confirmed_anchor_ids: z.array(idSchema),
  confirmed_by: z.string().trim().min(1),
  confirmed_at: isoTimeSchema,
}).strict();
const manifestDataSchema = z.object({
  schema_version: z.literal(MCP_SCHEMA_VERSION),
  run_id: idSchema,
  status: z.string(),
  stages: z.array(stageSchema),
}).strict();

type McpEnvelope = { ok: boolean; data?: unknown; error?: unknown };
export interface ThemeChokepointMcp {
  start(input: Record<string, unknown>): Promise<McpEnvelope>;
  getRun(runId: string): Promise<McpEnvelope>;
  getPendingAnchors(runId: string): Promise<McpEnvelope>;
  confirmAnchors(
    runId: string,
    ids: string[],
    actor: string,
  ): Promise<McpEnvelope>;
  continueRun(runId: string): Promise<McpEnvelope>;
  getArtifacts(runId: string): Promise<McpEnvelope>;
  close?(): Promise<void>;
}

export class M0Error extends Error {
  readonly code: string;
  readonly retryable: boolean;

  constructor(code: string, message: string, retryable = false) {
    super(`M0:${code}:${message}`);
    this.name = "M0Error";
    this.code = code;
    this.retryable = retryable;
  }

  toJSON() {
    return {
      name: this.name,
      code: this.code,
      message: this.message.replace(/^M0:[A-Z_]+:/, ""),
      retryable: this.retryable,
    };
  }
}

function invalid(message = "invalid M0 argument"): never {
  throw new M0Error("INVALID_ARGUMENT", message);
}

function parseInput<T>(schema: z.ZodType<T>, value: unknown): T {
  const parsed = schema.safeParse(value);
  if (!parsed.success) invalid();
  return parsed.data;
}

function parseEnvelope<T>(
  schema: z.ZodType<T>,
  value: unknown,
  context?: { duplicateResume?: boolean },
): T {
  const parsed = envelope(schema).safeParse(value);
  if (!parsed.success) {
    throw new M0Error(
      "MCP_RESPONSE_SCHEMA_MISMATCH",
      "MCP response did not match the required schema",
    );
  }
  if (!parsed.data.ok) {
    const code = context?.duplicateResume
      && parsed.data.error.code === "RUN_NOT_AWAITING_CONFIRMATION"
      ? "CONCURRENT_OR_DUPLICATE_RESUME"
      : parsed.data.error.code;
    throw new M0Error(code, "Python lifecycle operation failed", parsed.data.error.retryable);
  }
  return parsed.data.data;
}

function status(value: string): PythonStatus {
  const parsed = knownStatusSchema.safeParse(value);
  if (!parsed.success) {
    throw new M0Error("UNKNOWN_PYTHON_STATUS", "Python returned an unknown status");
  }
  return parsed.data;
}

function correlate(actual: string, expected: string): void {
  if (actual !== expected) {
    throw new M0Error(
      "RUN_CORRELATION_MISMATCH",
      "Python run correlation did not match",
    );
  }
}

const workflowInputSchema = researchRequestSchema.extend({
  mastraRunId: idSchema,
  pythonRunId: idSchema,
}).strict();

const confirmationStateSchema = z.object({
  actor: z.string(),
  selectedAnchorIds: z.array(idSchema),
  confirmedAt: isoTimeSchema,
}).strict();

const stateSchema = z.object({
  schemaVersion: z.literal("theme-chokepoint-m0-state.v1"),
  mastraRunId: idSchema,
  pythonRunId: idSchema,
  lastPythonStatus: knownStatusSchema,
  pendingAnchorIds: z.array(idSchema),
  confirmation: confirmationStateSchema.nullable(),
}).strict();
type M0State = z.infer<typeof stateSchema>;

const startedSchema = workflowInputSchema.extend({
  pythonStatus: knownStatusSchema,
}).strict();

export const resumeSchema = z.object({
  actor: z.string().trim().min(1),
  selectedAnchorIds: z.array(idSchema).min(1).refine(
    (ids) => new Set(ids).size === ids.length,
    "selectedAnchorIds must be unique",
  ),
}).strict();

export const suspendSchema = z.object({
  pythonRunId: idSchema,
  anchors: z.array(anchorSchema),
}).strict();

const gateSchema = z.object({
  mastraRunId: idSchema,
  pythonRunId: idSchema,
  pythonStatus: knownStatusSchema,
  gate: z.enum(["confirmed", "needs_clarification", "current"]),
  confirmation: confirmationStateSchema.nullable(),
}).strict();

const workflowOutputSchema = z.object({
  mastraRunId: idSchema,
  pythonRunId: idSchema,
  pythonStatus: knownStatusSchema,
  stages: z.array(stageSchema),
}).strict();

function snakeRequest(
  input: z.infer<typeof workflowInputSchema>,
): Record<string, unknown> {
  return {
    run_id: input.pythonRunId,
    theme: input.theme,
    trigger: input.trigger,
    region: input.region,
    as_of_date: input.asOfDate,
    time_horizon_months: input.timeHorizonMonths,
    analysis_goal: input.analysisGoal,
    seed_products: input.seedProducts,
    seed_companies: input.seedCompanies,
    research_mode: input.researchMode,
    max_depth: input.maxDepth,
    max_nodes: input.maxNodes,
    max_iterations: input.maxIterations,
    max_sources: input.maxSources,
    max_time_seconds: input.maxTimeSeconds,
    max_cost_usd: input.maxCostUsd,
    max_product_anchors: input.maxProductAnchors,
  };
}

function buildWorkflow(mcp: ThemeChokepointMcp) {
  const startPython = createStep({
    id: "start-python-stage1",
    inputSchema: workflowInputSchema,
    outputSchema: startedSchema,
    stateSchema,
    execute: async ({ inputData, state: current, setState }) => {
      const data = parseEnvelope(
        startDataSchema,
        await mcp.start(snakeRequest(inputData)),
      );
      correlate(data.run_id, inputData.pythonRunId);
      const pythonStatus = status(data.status);
      await setState({ ...current, lastPythonStatus: pythonStatus });
      return { ...inputData, pythonStatus };
    },
  });

  const awaitConfirmation = createStep({
    id: SUSPEND_STEP_ID,
    inputSchema: startedSchema,
    outputSchema: gateSchema,
    stateSchema,
    resumeSchema,
    suspendSchema,
    execute: async ({ inputData, resumeData, suspend, state: current, setState }) => {
      const run = parseEnvelope(
        runDataSchema,
        await mcp.getRun(inputData.pythonRunId),
      );
      correlate(run.run_id, inputData.pythonRunId);
      const pythonStatus = status(run.status);

      if (pythonStatus === "AWAITING_PRODUCT_CONFIRMATION") {
        const pending = parseEnvelope(
          pendingDataSchema,
          await mcp.getPendingAnchors(inputData.pythonRunId),
        );
        correlate(pending.run_id, inputData.pythonRunId);
        if (status(pending.status) !== "AWAITING_PRODUCT_CONFIRMATION") {
          throw new M0Error(
            "PYTHON_STATUS_MISMATCH",
            "pending anchors did not match the confirmation status",
          );
        }
        const pendingAnchorIds = pending.anchors.map((item) => item.anchor_id);
        await setState({
          ...current,
          lastPythonStatus: pythonStatus,
          pendingAnchorIds,
        });
        if (!resumeData) {
          return await suspend({
            pythonRunId: inputData.pythonRunId,
            anchors: pending.anchors,
          });
        }
        const selected = resumeSchema.parse(resumeData);
        if (selected.selectedAnchorIds.some((id) => !pendingAnchorIds.includes(id))) {
          throw new M0Error(
            "ANCHOR_NOT_PROPOSED_FOR_RUN",
            "selected anchor is not pending for this Python run",
          );
        }
        let receipt;
        try {
          receipt = parseEnvelope(
            confirmationDataSchema,
            await mcp.confirmAnchors(
              inputData.pythonRunId,
              selected.selectedAnchorIds,
              selected.actor,
            ),
            { duplicateResume: true },
          );
        } catch (error) {
          if (error instanceof M0Error) throw error;
          throw new M0Error("MCP_TOOL_FAILURE", "Python confirmation failed");
        }
        correlate(receipt.run_id, inputData.pythonRunId);
        if (status(receipt.status) !== "READY_FOR_SUPPLY_CHAIN") {
          throw new M0Error(
            "PYTHON_STATUS_MISMATCH",
            "confirmation did not reach READY_FOR_SUPPLY_CHAIN",
          );
        }
        const confirmation = {
          actor: receipt.confirmed_by,
          selectedAnchorIds: receipt.confirmed_anchor_ids,
          confirmedAt: receipt.confirmed_at,
        };
        await setState({
          ...current,
          lastPythonStatus: "READY_FOR_SUPPLY_CHAIN",
          pendingAnchorIds,
          confirmation,
        });
        return {
          mastraRunId: inputData.mastraRunId,
          pythonRunId: inputData.pythonRunId,
          pythonStatus: "READY_FOR_SUPPLY_CHAIN" as const,
          gate: "confirmed" as const,
          confirmation,
        };
      }

      if (resumeData || pythonStatus === "READY_FOR_SUPPLY_CHAIN") {
        throw new M0Error(
          "CONCURRENT_OR_DUPLICATE_RESUME",
          "run was already resumed or confirmed",
        );
      }
      await setState({ ...current, lastPythonStatus: pythonStatus });
      return {
        mastraRunId: inputData.mastraRunId,
        pythonRunId: inputData.pythonRunId,
        pythonStatus,
        gate: pythonStatus === "NEEDS_CLARIFICATION"
          ? "needs_clarification" as const
          : "current" as const,
        confirmation: null,
      };
    },
  });

  const continuePython = createStep({
    id: "continue-python-run",
    inputSchema: gateSchema,
    outputSchema: workflowOutputSchema,
    stateSchema,
    execute: async ({ inputData, state: current, setState }) => {
      if (inputData.gate !== "confirmed" || !inputData.confirmation) {
        return {
          mastraRunId: inputData.mastraRunId,
          pythonRunId: inputData.pythonRunId,
          pythonStatus: inputData.pythonStatus,
          stages: [],
        };
      }
      const manifest = parseEnvelope(
        manifestDataSchema,
        await mcp.continueRun(inputData.pythonRunId),
      );
      correlate(manifest.run_id, inputData.pythonRunId);
      const pythonStatus = status(manifest.status);
      await setState({ ...current, lastPythonStatus: pythonStatus });
      return {
        mastraRunId: inputData.mastraRunId,
        pythonRunId: inputData.pythonRunId,
        pythonStatus,
        stages: manifest.stages,
      };
    },
  });

  return createWorkflow({
    id: WORKFLOW_ID,
    description: "Durable Python-authoritative Theme Chokepoint M0 lifecycle.",
    inputSchema: workflowInputSchema,
    outputSchema: workflowOutputSchema,
    stateSchema,
  }).then(startPython).then(awaitConfirmation).then(continuePython).commit();
}

export type StartResult = {
  mastraRunId: string;
  pythonRunId: string;
  workflowStatus: string;
  pythonStatus: PythonStatus;
  resumable: boolean;
  anchors: Anchor[];
};

export type StatusResult = {
  mastraRunId: string;
  pythonRunId: string;
  workflowStatus: string;
  pythonStatus: PythonStatus;
  resumable: boolean;
};

export type ArtifactsResult = {
  mastraRunId: string;
  pythonRunId: string;
  pythonStatus: PythonStatus;
  stages: Array<{
    stage: number;
    inputStatus: string | null;
    outputStatus: string;
    outcome: string;
    artifactIds: string[];
    completedAt: string;
  }>;
};

function workflowFailure(result: { status: string; error?: unknown }): never {
  if (result.error instanceof M0Error) throw result.error;
  const candidate = result.error as {
    code?: unknown;
    message?: unknown;
    cause?: { code?: unknown; message?: unknown };
  } | undefined;
  const directCode = typeof candidate?.code === "string" ? candidate.code : undefined;
  const causeCode = typeof candidate?.cause?.code === "string"
    ? candidate.cause.code
    : undefined;
  const encoded = [candidate?.message, candidate?.cause?.message]
    .find((message): message is string => typeof message === "string")
    ?.match(/M0:([A-Z_]+):/u)?.[1];
  const recovered = directCode ?? causeCode ?? encoded;
  if (recovered) {
    throw new M0Error(recovered, "M0 workflow execution failed");
  }
  throw new M0Error("MCP_TOOL_FAILURE", "M0 workflow execution failed");
}

function stateFrom(snapshot: { initialState?: Record<string, unknown> }): M0State {
  const parsed = stateSchema.safeParse(snapshot.initialState);
  if (!parsed.success) {
    throw new M0Error("STORAGE_FAILURE", "persisted M0 correlation is invalid");
  }
  return parsed.data;
}

function persistedWorkflowStatus(snapshot: {
  status: string;
  steps?: Record<string, unknown>;
}, pythonStatus?: PythonStatus): string {
  const step = snapshot.steps?.[SUSPEND_STEP_ID] as
    | { status?: unknown }
    | Array<{ status?: unknown }>
    | undefined;
  const suspended = Array.isArray(step)
    ? step.some((item) => item.status === "suspended")
    : step?.status === "suspended";
  if (
    suspended
    || (
      snapshot.status === "pending"
      && pythonStatus === "AWAITING_PRODUCT_CONFIRMATION"
    )
  ) {
    return "suspended";
  }
  return snapshot.status;
}

export class ThemeChokepointM0Service {
  readonly suspendStepId = SUSPEND_STEP_ID;

  constructor(
    readonly workflow: ReturnType<typeof buildWorkflow>,
    private readonly mcp: ThemeChokepointMcp,
    private readonly storage: LibSQLStore,
  ) {}

  async startM0(value: ResearchRequestInput): Promise<StartResult> {
    const input = parseInput(researchRequestSchema, value);
    const mastraRunId = randomUUID();
    const pythonRunId = mastraRunId;
    const initialState: M0State = {
      schemaVersion: "theme-chokepoint-m0-state.v1",
      mastraRunId,
      pythonRunId,
      lastPythonStatus: "REQUEST_STORED",
      pendingAnchorIds: [],
      confirmation: null,
    };
    const run = await this.workflow.createRun({ runId: mastraRunId });
    const result = await run.start({
      inputData: { ...input, mastraRunId, pythonRunId },
      initialState,
    });
    if (result.status === "failed") workflowFailure(result);
    if (result.status === "suspended") {
      const step = result.steps[SUSPEND_STEP_ID];
      const payload = parseInput(
        suspendSchema,
        Array.isArray(step) ? undefined : step?.suspendPayload,
      );
      return {
        mastraRunId,
        pythonRunId,
        workflowStatus: result.status,
        pythonStatus: "AWAITING_PRODUCT_CONFIRMATION",
        resumable: true,
        anchors: payload.anchors,
      };
    }
    if (result.status !== "success") workflowFailure(result);
    return {
      mastraRunId,
      pythonRunId,
      workflowStatus: result.status,
      pythonStatus: result.result.pythonStatus,
      resumable: false,
      anchors: [],
    };
  }

  async resumeM0(value: {
    mastraRunId: string;
    actor: string;
    selectedAnchorIds: string[];
  }): Promise<StatusResult> {
    const parsed = parseInput(
      resumeSchema.extend({ mastraRunId: idSchema }).strict(),
      value,
    );
    const snapshot = await this.workflow.getWorkflowRunById(parsed.mastraRunId, {
      fields: ["steps", "result"],
    });
    if (!snapshot) {
      throw new M0Error("MASTRA_RUN_NOT_FOUND", "Mastra run was not found");
    }
    const persisted = stateFrom(snapshot);
    const live = parseEnvelope(
      runDataSchema,
      await this.mcp.getRun(persisted.pythonRunId),
    );
    correlate(live.run_id, persisted.pythonRunId);
    if (status(live.status) !== "AWAITING_PRODUCT_CONFIRMATION") {
      throw new M0Error(
        "CONCURRENT_OR_DUPLICATE_RESUME",
        "run was already resumed or confirmed",
      );
    }
    const pending = parseEnvelope(
      pendingDataSchema,
      await this.mcp.getPendingAnchors(persisted.pythonRunId),
    );
    correlate(pending.run_id, persisted.pythonRunId);
    const pendingIds = new Set(pending.anchors.map((item) => item.anchor_id));
    if (parsed.selectedAnchorIds.some((id) => !pendingIds.has(id))) {
      throw new M0Error(
        "ANCHOR_NOT_PROPOSED_FOR_RUN",
        "selected anchor is not pending for this Python run",
      );
    }
    const run = await this.workflow.createRun({ runId: parsed.mastraRunId });
    let result;
    try {
      result = await run.resume({
        step: SUSPEND_STEP_ID,
        resumeData: {
          actor: parsed.actor,
          selectedAnchorIds: parsed.selectedAnchorIds,
        },
      });
    } catch {
      throw new M0Error(
        "CONCURRENT_OR_DUPLICATE_RESUME",
        "run was concurrently or previously resumed",
      );
    }
    if (result.status === "failed") {
      if (
        result.error instanceof M0Error
        && result.error.code === "RUN_NOT_AWAITING_CONFIRMATION"
      ) {
        throw new M0Error(
          "CONCURRENT_OR_DUPLICATE_RESUME",
          "run was concurrently resumed",
        );
      }
      workflowFailure(result);
    }
    if (result.status !== "success") {
      throw new M0Error(
        "CONCURRENT_OR_DUPLICATE_RESUME",
        "run did not complete its confirmation resume",
      );
    }
    correlate(result.result.pythonRunId, persisted.pythonRunId);
    return {
      mastraRunId: parsed.mastraRunId,
      pythonRunId: persisted.pythonRunId,
      workflowStatus: result.status,
      pythonStatus: result.result.pythonStatus,
      resumable: false,
    };
  }

  async getM0Status(mastraRunId: string): Promise<StatusResult> {
    const normalized = parseInput(idSchema, mastraRunId);
    const snapshot = await this.workflow.getWorkflowRunById(normalized, {
      fields: ["steps", "result"],
    });
    if (!snapshot) {
      throw new M0Error("MASTRA_RUN_NOT_FOUND", "Mastra run was not found");
    }
    const persisted = stateFrom(snapshot);
    const live = parseEnvelope(
      runDataSchema,
      await this.mcp.getRun(persisted.pythonRunId),
    );
    correlate(live.run_id, persisted.pythonRunId);
    const pythonStatus = status(live.status);
    const workflowStatus = persistedWorkflowStatus(snapshot, pythonStatus);
    return {
      mastraRunId: normalized,
      pythonRunId: persisted.pythonRunId,
      workflowStatus,
      pythonStatus,
      resumable: workflowStatus === "suspended"
        && pythonStatus === "AWAITING_PRODUCT_CONFIRMATION",
    };
  }

  async getM0Artifacts(mastraRunId: string): Promise<ArtifactsResult> {
    const normalized = parseInput(idSchema, mastraRunId);
    const snapshot = await this.workflow.getWorkflowRunById(normalized);
    if (!snapshot) {
      throw new M0Error("MASTRA_RUN_NOT_FOUND", "Mastra run was not found");
    }
    const persisted = stateFrom(snapshot);
    const manifest = parseEnvelope(
      manifestDataSchema,
      await this.mcp.getArtifacts(persisted.pythonRunId),
    );
    correlate(manifest.run_id, persisted.pythonRunId);
    return {
      mastraRunId: normalized,
      pythonRunId: persisted.pythonRunId,
      pythonStatus: status(manifest.status),
      stages: manifest.stages.map((item) => ({
        stage: item.stage,
        inputStatus: item.input_status,
        outputStatus: item.output_status,
        outcome: item.outcome,
        artifactIds: item.artifact_ids,
        completedAt: item.completed_at,
      })),
    };
  }

  async close(): Promise<void> {
    await this.mcp.close?.();
    await this.storage.close();
  }
}

type CreateM0Options = {
  mcp?: ThemeChokepointMcp;
  mcpUrl?: string;
  storageUrl?: string;
};

export async function createM0Service(
  options: CreateM0Options = {},
): Promise<ThemeChokepointM0Service> {
  const url = validateMcpUrl(
    options.mcpUrl ?? process.env.THEME_CHOKEPOINT_MCP_URL ?? DEFAULT_MCP_URL,
  );
  const mcp = options.mcp ?? createMcpAdapter(url);
  const storageUrl = options.storageUrl
    ?? process.env.MASTRA_STORAGE_URL
    ?? "file:./theme-chokepoint-mastra.db";
  const storage = new LibSQLStore({
    id: "theme-chokepoint-m0-storage",
    url: storageUrl,
  });
  try {
    await storage.init();
    const definition = buildWorkflow(mcp);
    const mastra = new Mastra({
      storage,
      logger: false,
      workflows: { themeChokepointM0: definition },
    });
    const workflow = mastra.getWorkflow("themeChokepointM0");
    return new ThemeChokepointM0Service(workflow, mcp, storage);
  } catch (error) {
    await storage.close().catch(() => undefined);
    if (error instanceof M0Error) throw error;
    throw new M0Error("STORAGE_FAILURE", "M0 persistent storage initialization failed");
  }
}

export function validateMcpUrl(value: string): URL {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return invalid("THEME_CHOKEPOINT_MCP_URL is invalid");
  }
  if (
    url.protocol !== "http:"
    || !["127.0.0.1", "localhost"].includes(url.hostname)
    || !url.port
    || url.pathname !== "/mcp"
    || url.username
    || url.password
    || url.search
    || url.hash
  ) {
    return invalid("THEME_CHOKEPOINT_MCP_URL must be a local /mcp endpoint");
  }
  return url;
}

export function createMcpAdapter(url: URL): ThemeChokepointMcp {
  const serverName = "themeChokepoint";
  const client = new MCPClient({
    id: `theme-chokepoint-m0-${randomUUID()}`,
    servers: {
      [serverName]: {
        url,
        allowedHosts: [url.host],
      },
    },
  });
  let toolsPromise: ReturnType<MCPClient["listTools"]> | undefined;
  const call = async (name: string, input: Record<string, unknown>) => {
    toolsPromise ??= client.listTools();
    const tools = await toolsPromise;
    const tool = tools[`${serverName}_${name}`];
    if (!tool?.execute) {
      throw new M0Error("MCP_TOOL_FAILURE", "required MCP lifecycle tool is unavailable");
    }
    try {
      return await tool.execute(input, {
        requestContext: new RequestContext(),
      } as never) as McpEnvelope;
    } catch {
      throw new M0Error("MCP_TOOL_FAILURE", "MCP lifecycle tool failed");
    }
  };
  return {
    start: (input) => call("theme_chokepoint_start", input),
    getRun: (runId) => call("theme_chokepoint_get_run", { run_id: runId }),
    getPendingAnchors: (runId) => call(
      "theme_chokepoint_get_pending_anchors",
      { run_id: runId },
    ),
    confirmAnchors: (runId, ids, actor) => call(
      "theme_chokepoint_confirm_anchors",
      { run_id: runId, anchor_ids: ids, confirmed_by: actor },
    ),
    continueRun: (runId) => call("theme_chokepoint_continue", { run_id: runId }),
    getArtifacts: (runId) => call(
      "theme_chokepoint_get_artifacts",
      { run_id: runId },
    ),
    close: () => client.disconnect(),
  };
}
