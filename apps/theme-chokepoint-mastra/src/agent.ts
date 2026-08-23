import { Agent } from "@mastra/core/agent";
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

type OperatorService = {
  startM0(input: unknown): Promise<unknown>;
  getM0Status(input: unknown): Promise<unknown>;
  getM0PendingAnchors(input: unknown): Promise<unknown>;
  resumeM0(input: unknown): Promise<unknown>;
  getM0Artifacts(input: unknown): Promise<unknown>;
  importHistoricalRuns(input: unknown): Promise<unknown>;
};

const runId = z.string().trim().min(1);

export function createThemeChokepointAgent(service: OperatorService) {
  const startThemeResearch = createTool({
    id: "start-theme-research",
    description: "Start an authoritative Python Theme Chokepoint run.",
    inputSchema: z
      .object({
        mastraRunId: runId,
        pythonRequest: z.record(z.string(), z.unknown()),
      })
      .strict(),
    execute: async (input) => service.startM0(input),
  });
  const getThemeStatus = createTool({
    id: "get-theme-status",
    description: "Read the correlated Python run status.",
    inputSchema: z.object({ mastraRunId: runId }).strict(),
    execute: async (input) => service.getM0Status(input),
  });
  const getThemePendingAnchors = createTool({
    id: "get-theme-pending-anchors",
    description: "Read product anchors awaiting explicit human confirmation.",
    inputSchema: z.object({ mastraRunId: runId }).strict(),
    execute: async (input) => service.getM0PendingAnchors(input),
  });
  const confirmAndResumeThemeResearch = createTool({
    id: "confirm-and-resume-theme-research",
    description: "Record an explicit actor confirmation and resume its run.",
    inputSchema: z
      .object({
        mastraRunId: runId,
        actor: z.string().trim().min(1),
        selectedAnchorIds: z.array(z.string().trim().min(1)).min(1),
      })
      .strict(),
    execute: async (input) => service.resumeM0(input),
  });
  const getThemeArtifacts = createTool({
    id: "get-theme-artifacts",
    description: "Read the Python manifest and its Studio projection.",
    inputSchema: z.object({ mastraRunId: runId }).strict(),
    execute: async (input) => service.getM0Artifacts(input),
  });
  const importHistoricalThemeRuns = createTool({
    id: "import-historical-theme-runs",
    description: "Create idempotent read-only Studio projections of Python runs.",
    inputSchema: z
      .object({ pythonRunIds: z.array(runId).optional() })
      .strict(),
    execute: async (input) => service.importHistoricalRuns(input),
  });

  return new Agent({
    id: "theme-chokepoint-operator",
    name: "Theme Chokepoint Operator",
    description: "Operates the Python-authoritative Theme Chokepoint lifecycle.",
    instructions: [
      "Use only the registered lifecycle and projection tools.",
      "Python RootStageOrchestrator is the sole state-machine authority.",
      "Never infer confirmation, score evidence, access storage directly, or bypass a suspended human gate.",
      "Historical imports are read-only projections and are not native research executions.",
    ],
    model: "openai/gpt-5.2",
    tools: {
      startThemeResearch,
      getThemeStatus,
      getThemePendingAnchors,
      confirmAndResumeThemeResearch,
      getThemeArtifacts,
      importHistoricalThemeRuns,
    },
  });
}
