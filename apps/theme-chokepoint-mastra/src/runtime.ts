import { Mastra } from "@mastra/core";
import { LibSQLStore } from "@mastra/libsql";
import { z } from "zod";

import { failure, type Failure } from "./errors.js";
import { MastraMcpPythonClient } from "./mcp-client.js";
import {
  decodeFailureResponse,
  decodeRunResponse,
  manifestEnvelopeSchema,
  manifestIsSemanticallyValid,
} from "./schemas.js";
import { M0Service } from "./service.js";
import { LibSqlM0StateStore } from "./state.js";
import { createM0Workflow } from "./workflow.js";

const optionsSchema = z
  .object({
    databasePath: z.string().min(1),
    mcpUrl: z.string().url(),
    mcpTimeoutMs: z.number().int().min(1_000).max(1_800_000).default(5_000),
  })
  .strict();

export async function createM0Runtime(options: unknown) {
  const parsed = optionsSchema.parse(options);
  const state = await LibSqlM0StateStore.open(parsed.databasePath);
  const mastraStorage = new LibSQLStore({
    id: "theme-chokepoint-m0-storage",
    url: `file:${parsed.databasePath}.mastra`,
  });
  await mastraStorage.init();
  const python = new MastraMcpPythonClient({
    url: parsed.mcpUrl,
    timeoutMs: parsed.mcpTimeoutMs,
  });
  const workflow = createM0Workflow({
    continuePythonRun: (correlation) =>
      reconcileAndContinuePython(python, correlation),
  });
  const mastra = new Mastra({
    storage: mastraStorage,
    workflows: { themeChokepointM0: workflow },
  });
  const workflowPort = new MastraWorkflowPort(
    mastra.getWorkflow("themeChokepointM0"),
  );
  const service = new M0Service({
    store: state,
    workflow: workflowPort,
    python,
  });
  return {
    mastra,
    service,
    async close() {
      await python.close();
      await state.close();
      await mastraStorage.close();
    },
  };
}

type Correlation = {
  mastraRunId: string;
  pythonRunId: string;
  workflowId: string;
};

type RegisteredWorkflow = ReturnType<Mastra["getWorkflow"]>;

class MastraWorkflowPort {
  constructor(private readonly workflow: RegisteredWorkflow) {}

  async getSnapshot(mastraRunId: string): Promise<Correlation | null> {
    const state = await this.workflow.getWorkflowRunById(mastraRunId);
    if (!state) return null;
    const parsed = z
      .object({
        mastraRunId: z.string().min(1),
        pythonRunId: z.string().min(1),
      })
      .strict()
      .safeParse(state.payload);
    if (!parsed.success) return null;
    return {
      ...parsed.data,
      workflowId: "theme-chokepoint-m0",
    };
  }

  async start(correlation: Correlation) {
    const existing = await this.workflow.getWorkflowRunById(
      correlation.mastraRunId,
    );
    if (existing) {
      const payload = await this.getSnapshot(correlation.mastraRunId);
      if (
        !payload ||
        payload.pythonRunId !== correlation.pythonRunId ||
        existing.status !== "suspended"
      ) {
        throw new Error("workflow start conflict");
      }
      return existing;
    }
    const run = await this.workflow.createRun({ runId: correlation.mastraRunId });
    return run.start({
      inputData: {
        mastraRunId: correlation.mastraRunId,
        pythonRunId: correlation.pythonRunId,
      },
    });
  }

  async resume(correlation: Correlation, confirmation: {
    runId: string;
    confirmedAnchorIds: string[];
    confirmedBy: string;
    confirmedAt: string;
  }) {
    const state = await this.workflow.getWorkflowRunById(
      correlation.mastraRunId,
    );
    if (!state) return null;
    const run = await this.workflow.createRun({ runId: correlation.mastraRunId });
    if (state.status === "success") {
      return { status: "success" as const, result: state.result };
    }
    if (state.status === "running") {
      return run.restart();
    }
    if (state.status !== "suspended") {
      return { status: state.status };
    }
    const suspendedSteps = Object.keys(state.suspendedPaths ?? {});
    if (suspendedSteps.includes("suspend-for-confirmation")) {
      return run.resume({
        step: "suspend-for-confirmation",
        resumeData: {
          mastraRunId: correlation.mastraRunId,
          pythonRunId: correlation.pythonRunId,
          confirmation,
        },
      });
    }
    if (suspendedSteps.includes("continue-python-run")) {
      return run.resume({
        step: "continue-python-run",
        resumeData: {
          mastraRunId: correlation.mastraRunId,
          pythonRunId: correlation.pythonRunId,
        },
      });
    }
    return { status: "failed" as const };
  }
}

async function reconcileAndContinuePython(
  python: MastraMcpPythonClient,
  correlation: { mastraRunId: string; pythonRunId: string },
): Promise<{ ok: true } | Failure> {
  try {
    const runRaw = await python.call("theme_chokepoint_get_run", {
      run_id: correlation.pythonRunId,
    });
    const runFailure = decodeFailureResponse(runRaw);
    if (runFailure) return runFailure;
    const run = decodeRunResponse(runRaw, correlation.pythonRunId);
    if (run.kind === "unknown-status") return failure("UNKNOWN_PYTHON_STATUS");
    if (run.kind === "schema-mismatch") {
      return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
    }
    if (run.data.confirmation === null) {
      return failure("PYTHON_STATUS_MISMATCH");
    }
    const tool =
      run.data.status === "READY_FOR_SUPPLY_CHAIN"
        ? "theme_chokepoint_continue"
        : "theme_chokepoint_get_artifacts";
    const raw = await python.call(tool, { run_id: correlation.pythonRunId });
    const pythonFailure = decodeFailureResponse(raw);
    if (pythonFailure) return pythonFailure;
    const manifest = manifestEnvelopeSchema.safeParse(raw);
    if (
      !manifest.success ||
      !manifestIsSemanticallyValid(
        manifest.data.data,
        correlation.pythonRunId,
        tool === "theme_chokepoint_get_artifacts"
          ? run.data.status
          : undefined,
      )
    ) {
      return failure("MCP_RESPONSE_SCHEMA_MISMATCH");
    }
    return { ok: true };
  } catch (error) {
    const retryable =
      typeof error === "object" &&
      error !== null &&
      "code" in error &&
      error.code === "MCP_TOOL_FAILURE" &&
      "retryable" in error &&
      error.retryable === true;
    return failure("MCP_TOOL_FAILURE", retryable);
  }
}
