import { Mastra } from "@mastra/core";
import { LibSQLStore } from "@mastra/libsql";
import { z } from "zod";

import { createThemeChokepointAgent } from "./agent.js";
import {
  importHistoricalRuns,
  type HistoricalProjectionWorkflowPort,
} from "./historical-import.js";
import { createHistoricalProjectionWorkflow } from "./historical-workflow.js";
import { MastraMcpPythonClient } from "./mcp-client.js";
import { projectPythonStage } from "./projection.js";
import { M0Service } from "./service.js";
import { LibSqlM0StateStore } from "./state.js";
import { createM0Workflow } from "./workflow.js";

const optionsSchema = z
  .object({
    databasePath: z.string().min(1),
    mcpUrl: z.string().url(),
    mcpTimeoutMs: z.number().int().min(1_000).max(1_800_000).default(5_000),
    autoImportHistoricalRuns: z.boolean().default(false),
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
    projectStage1: (correlation) => projectPythonStage(python, correlation, 1),
    advancePythonStage: (correlation, stage) =>
      projectPythonStage(python, correlation, stage),
  });
  const historicalWorkflow = createHistoricalProjectionWorkflow();
  const mastra = new Mastra({
    storage: mastraStorage,
    workflows: {
      themeChokepointM0: workflow,
      themeChokepointHistoricalProjection: historicalWorkflow,
    },
  });
  const workflowPort = new MastraWorkflowPort(
    mastra.getWorkflow("themeChokepointM0"),
  );
  const service = new M0Service({
    store: state,
    workflow: workflowPort,
    python,
    importer: {
      importRuns: (runIds) =>
        importHistoricalRuns(
          python,
          new MastraHistoricalProjectionPort(
            mastra.getWorkflow("themeChokepointHistoricalProjection"),
          ),
          runIds,
        ),
    },
  });
  mastra.addAgent(
    createThemeChokepointAgent(service),
    "themeChokepointOperator",
  );
  const startupHistoricalImport = parsed.autoImportHistoricalRuns
    ? await service.importHistoricalRuns({})
    : null;
  return {
    mastra,
    service,
    startupHistoricalImport,
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
    const stage = suspendedSteps.find((step) => /^stage-[2-7]-/.test(step));
    if (stage) {
      return run.resume({
        step: stage,
        resumeData: {
          mastraRunId: correlation.mastraRunId,
          pythonRunId: correlation.pythonRunId,
        },
      });
    }
    return { status: "failed" as const };
  }
}

class MastraHistoricalProjectionPort
  implements HistoricalProjectionWorkflowPort
{
  constructor(private readonly workflow: RegisteredWorkflow) {}

  async get(mastraRunId: string): Promise<unknown | null> {
    const state = await this.workflow.getWorkflowRunById(mastraRunId);
    if (!state) return null;
    return state.result ?? state.payload ?? null;
  }

  async create(mastraRunId: string, input: import("./workflow.js").WorkflowProjectionState) {
    const existing = await this.workflow.getWorkflowRunById(mastraRunId);
    if (existing) return;
    const run = await this.workflow.createRun({ runId: mastraRunId });
    const result = await run.start({ inputData: input });
    if (result.status !== "success") {
      throw new Error("historical projection workflow failed");
    }
  }
}
