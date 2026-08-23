import { expect, it } from "vitest";

import { projectPythonStage } from "../src/projection.js";

const receipt = {
  schema_version: "theme-chokepoint-mcp.v1",
  run_id: "python-1",
  confirmed_anchor_ids: ["anchor-1"],
  confirmed_by: "owner",
  confirmed_at: "2026-08-23T11:00:00+00:00",
};

function run(status: string, nextStage: number | null) {
  return { ok: true, data: { ...receipt, status, next_stage: nextStage } };
}

function manifest(finalStatus: string, stages: number[]) {
  return {
    ok: true,
    data: {
      run_id: "python-1",
      contract_id: "theme-chokepoint-scoring-v1.4",
      executable_contract_id: "contract-v1",
      executable_contract_sha256: "a".repeat(64),
      stages: stages.map((stage) => ({
        stage,
        input_status: stage === 1 ? null : stage === 2 ? "READY_FOR_SUPPLY_CHAIN" : "SUPPLY_CHAIN_GRAPH_READY",
        output_status:
          stage === 1
            ? "AWAITING_PRODUCT_CONFIRMATION"
            : stage === 2
              ? "SUPPLY_CHAIN_GRAPH_READY"
              : finalStatus,
        outcome: finalStatus.includes("INCOMPLETE") && stage === 3 ? "incomplete" : "completed",
        artifact_ids: [`artifact-${stage}`],
        completed_at: "2026-08-23T11:00:00+00:00",
      })),
      final_status: finalStatus,
      created_at: "2026-08-23T10:00:00+00:00",
      updated_at: "2026-08-23T11:00:00+00:00",
    },
  };
}

it("dispatches exactly the Python stage selected by the authoritative status", async () => {
  const calls: Array<[string, Record<string, unknown>]> = [];
  const python = {
    async call(tool: string, input: Record<string, unknown>) {
      calls.push([tool, input]);
      if (tool === "theme_chokepoint_get_run") return run("READY_FOR_SUPPLY_CHAIN", 2);
      if (tool === "theme_chokepoint_get_artifacts") {
        return manifest("READY_FOR_SUPPLY_CHAIN", [1]);
      }
      if (tool === "theme_chokepoint_advance_stage") {
        return manifest("SUPPLY_CHAIN_GRAPH_READY", [1, 2]);
      }
      throw new Error(`unexpected tool ${tool}`);
    },
  };

  await expect(
    projectPythonStage(python, { mastraRunId: "mastra-1", pythonRunId: "python-1" }, 2),
  ).resolves.toMatchObject({
    ok: true,
    update: {
      pythonStatus: "SUPPLY_CHAIN_GRAPH_READY",
      projection: { stage: 2, state: "completed", artifactIds: ["artifact-2"] },
    },
  });
  expect(calls.at(-1)).toEqual([
    "theme_chokepoint_advance_stage",
    {
      run_id: "python-1",
      expected_stage: 2,
      expected_status: "READY_FOR_SUPPLY_CHAIN",
      idempotency_key: "mastra-1:python-1:stage-2",
    },
  ]);
});

it("projects later wrappers as not entered after an authoritative terminal result", async () => {
  const calls: string[] = [];
  const python = {
    async call(tool: string) {
      calls.push(tool);
      if (tool === "theme_chokepoint_get_run") {
        return run("INCOMPLETE_BUDGET_EXHAUSTED", null);
      }
      if (tool === "theme_chokepoint_get_artifacts") {
        return manifest("INCOMPLETE_BUDGET_EXHAUSTED", [1, 2, 3]);
      }
      throw new Error("terminal run must not advance");
    },
  };

  await expect(
    projectPythonStage(python, { mastraRunId: "mastra-1", pythonRunId: "python-1" }, 4),
  ).resolves.toMatchObject({
    ok: true,
    update: {
      pythonStatus: "INCOMPLETE_BUDGET_EXHAUSTED",
      projection: { stage: 4, state: "not_entered", artifactIds: [] },
    },
  });
  expect(calls).toEqual([
    "theme_chokepoint_get_run",
    "theme_chokepoint_get_artifacts",
  ]);
});
