import { expect, it } from "vitest";

import {
  historicalProjectionRunId,
  importHistoricalRuns,
} from "../src/historical-import.js";
import { createHistoricalProjectionWorkflow } from "../src/historical-workflow.js";

const historicalManifest = {
  run_id: "python-old-1",
  contract_id: "theme-chokepoint-scoring-v1.4",
  executable_contract_id: "contract-v1",
  executable_contract_sha256: "a".repeat(64),
  stages: [
    {
      stage: 1,
      input_status: null,
      output_status: "AWAITING_PRODUCT_CONFIRMATION",
      outcome: "awaiting_confirmation",
      artifact_ids: ["anchor-old"],
      completed_at: "2026-08-20T10:01:00+00:00",
    },
    {
      stage: 2,
      input_status: "READY_FOR_SUPPLY_CHAIN",
      output_status: "SUPPLY_CHAIN_GRAPH_READY",
      outcome: "completed",
      artifact_ids: ["graph-old"],
      completed_at: "2026-08-20T10:02:00+00:00",
    },
    {
      stage: 3,
      input_status: "SUPPLY_CHAIN_GRAPH_READY",
      output_status: "INCOMPLETE_BUDGET_EXHAUSTED",
      outcome: "max_iterations:10",
      artifact_ids: ["assessment-old"],
      completed_at: "2026-08-20T11:00:00+00:00",
    },
  ],
  final_status: "INCOMPLETE_BUDGET_EXHAUSTED",
  created_at: "2026-08-20T10:00:00+00:00",
  updated_at: "2026-08-20T11:00:00+00:00",
};

it("registers a seven-node read-only workflow for imported Studio runs", () => {
  const workflow = createHistoricalProjectionWorkflow() as {
    id: string;
    steps: Record<string, unknown>;
  };

  expect(workflow.id).toBe("theme-chokepoint-historical-projection");
  expect(Object.keys(workflow.steps).sort()).toEqual([
    "import-stage-1",
    "import-stage-2",
    "import-stage-3",
    "import-stage-4",
    "import-stage-5",
    "import-stage-6",
    "import-stage-7",
  ]);
});

it("creates one immutable read-only Studio projection per historical Python run", async () => {
  const pythonCalls: string[] = [];
  const python = {
    async call(tool: string) {
      pythonCalls.push(tool);
      if (tool === "theme_chokepoint_list_runs") {
        return {
          ok: true,
          data: {
            schema_version: "theme-chokepoint-mcp.v1",
            run_ids: ["python-old-1"],
          },
        };
      }
      if (tool === "theme_chokepoint_get_artifacts") {
        return { ok: true, data: historicalManifest };
      }
      throw new Error(`mutating or unexpected tool: ${tool}`);
    },
  };
  const runs = new Map<string, unknown>();
  const workflow = {
    async get(runId: string) { return runs.get(runId) ?? null; },
    async create(runId: string, input: unknown) { runs.set(runId, input); },
  };

  const first = await importHistoricalRuns(python, workflow);
  const second = await importHistoricalRuns(python, workflow);

  expect(first).toEqual(second);
  expect(first).toMatchObject({
    ok: true,
    data: {
      imported: [{
        pythonRunId: "python-old-1",
        mastraRunId: historicalProjectionRunId("python-old-1"),
        imported: true,
      }],
    },
  });
  expect(runs.get(historicalProjectionRunId("python-old-1"))).toMatchObject({
    imported: true,
    pythonRunId: "python-old-1",
    pythonStatus: "INCOMPLETE_BUDGET_EXHAUSTED",
    originalCreatedAt: historicalManifest.created_at,
    originalUpdatedAt: historicalManifest.updated_at,
    stageResults: [
      { stage: 1, state: "completed" },
      { stage: 2, state: "completed" },
      { stage: 3, state: "incomplete" },
      { stage: 4, state: "not_entered" },
      { stage: 5, state: "not_entered" },
      { stage: 6, state: "not_entered" },
      { stage: 7, state: "not_entered" },
    ],
  });
  expect(
    pythonCalls.every((tool) =>
      ["theme_chokepoint_list_runs", "theme_chokepoint_get_artifacts"].includes(tool),
    ),
  ).toBe(true);
});
