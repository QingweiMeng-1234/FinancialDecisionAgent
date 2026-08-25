import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { Mastra } from "@mastra/core";
import { LibSQLStore } from "@mastra/libsql";
import { expect, it } from "vitest";

import { createM0Workflow } from "../src/workflow.js";

it("exposes Stage 1-7 as fixed projection wrappers around the Python authority", async () => {
  const workflow = createM0Workflow() as {
    id: string;
    steps: Record<string, { id: string }>;
  };

  expect(workflow.id).toBe("theme-chokepoint-m0");
  expect(Object.keys(workflow.steps).sort()).toEqual([
    "correlate-python-run",
    "project-stage-1",
    "select-stage-4-outcome",
    "select-stage-5-outcome",
    "select-stage-6-outcome",
    "select-stage-7-outcome",
    "skip-stage-4-not-entered",
    "skip-stage-5-not-entered",
    "skip-stage-6-not-entered",
    "skip-stage-7-not-entered",
    "stage-2-supply-chain",
    "stage-3-chokepoint",
    "stage-4-company-mapping",
    "stage-5-persistent-research",
    "stage-6-monitoring",
    "stage-7-signal-export",
    "suspend-for-confirmation",
  ]);
  const source = await readFile(
    new URL("../src/workflow.ts", import.meta.url),
    "utf8",
  );
  expect(source).not.toMatch(/sqlite|transition[_ -]?table/i);
  expect(source).toMatch(/createWorkflow|createStep/);
});

it("suspends before confirmation then enters each Python-backed stage wrapper once", async () => {
  const directory = await mkdtemp(join(tmpdir(), "m0-workflow-"));
  const storage = new LibSQLStore({
    id: "m0-workflow-test",
    url: `file:${join(directory, "mastra.sqlite")}`,
  });
  await storage.init();
  const dispatchedStages: number[] = [];
  const mastra = new Mastra({
    storage,
    workflows: {
      m0: createM0Workflow({
        projectStage1: async () => ({
          ok: true,
          update: {
            pythonStatus: "AWAITING_PRODUCT_CONFIRMATION",
            contractId: "theme-chokepoint-scoring-v1.4",
            executableContractId: "contract-v1",
            executableContractSha256: "a".repeat(64),
            originalCreatedAt: "2026-08-23T10:00:00+00:00",
            originalUpdatedAt: "2026-08-23T10:01:00+00:00",
            projection: {
              stage: 1,
              state: "completed",
              inputStatus: null,
              outputStatus: "AWAITING_PRODUCT_CONFIRMATION",
              outcome: "awaiting_confirmation",
              artifactIds: ["anchor-1"],
              completedAt: "2026-08-23T10:01:00+00:00",
            },
          },
        }),
        advancePythonStage: async (_correlation, stage) => {
          dispatchedStages.push(stage);
          return {
            ok: true,
            update: {
              pythonStatus: stage === 7 ? "SIGNAL_EXPORT_READY" : "READY_FOR_SUPPLY_CHAIN",
              contractId: "theme-chokepoint-scoring-v1.4",
              executableContractId: "contract-v1",
              executableContractSha256: "a".repeat(64),
              originalCreatedAt: "2026-08-23T10:00:00+00:00",
              originalUpdatedAt: "2026-08-23T11:00:00+00:00",
              projection: {
                stage,
                state: "completed",
                inputStatus: "READY_FOR_SUPPLY_CHAIN",
                outputStatus: stage === 7 ? "SIGNAL_EXPORT_READY" : "READY_FOR_SUPPLY_CHAIN",
                outcome: "completed",
                artifactIds: [`stage-${stage}`],
                completedAt: "2026-08-23T11:00:00+00:00",
              },
            },
          };
        },
      }),
    },
  });
  const workflow = mastra.getWorkflow("m0");
  const correlation = {
    mastraRunId: "mastra-1",
    pythonRunId: "python-1",
  };
  const correlatedResume = {
    mastraRunId: "mastra-1",
    pythonRunId: "python-1",
    confirmation: {
      runId: "python-1",
      confirmedAnchorIds: ["anchor-1"],
      confirmedBy: "owner",
      confirmedAt: "2026-08-23T11:00:00+00:00",
    },
  };

  try {
    const run = await workflow.createRun({ runId: "mastra-1" });
    await expect(run.start({ inputData: correlation })).resolves.toMatchObject({
      status: "suspended",
      suspendPayload: {
        "suspend-for-confirmation": {
          ...correlation,
          status: "AWAITING_PRODUCT_CONFIRMATION",
        },
      },
    });
    for (const resumeData of [
      { ...correlatedResume, mastraRunId: "mastra-other" },
      { ...correlatedResume, pythonRunId: "python-other" },
      {
        ...correlatedResume,
        confirmation: {
          ...correlatedResume.confirmation,
          runId: "python-other",
        },
      },
    ]) {
      await expect(run.resume({
        step: "suspend-for-confirmation",
        resumeData,
      })).resolves.toMatchObject({ status: "suspended" });
    }
    await expect(run.resume({
      step: "suspend-for-confirmation",
      resumeData: correlatedResume,
    })).resolves.toMatchObject({
      status: "success",
      result: {
        ...correlation,
        imported: false,
        pythonStatus: "SIGNAL_EXPORT_READY",
        stageResults: [
          { stage: 1, state: "completed", artifactIds: ["anchor-1"] },
          { stage: 2, state: "completed", artifactIds: ["stage-2"] },
          { stage: 3, state: "completed", artifactIds: ["stage-3"] },
          { stage: 4, state: "completed", artifactIds: ["stage-4"] },
          { stage: 5, state: "completed", artifactIds: ["stage-5"] },
          { stage: 6, state: "completed", artifactIds: ["stage-6"] },
          { stage: 7, state: "completed", artifactIds: ["stage-7"] },
        ],
      },
    });
    expect(dispatchedStages).toEqual([2, 3, 4, 5, 6, 7]);
  } finally {
    await storage.close();
  }
});

it("terminates after incomplete Stage 3 without entering Stage 4-7", async () => {
  const directory = await mkdtemp(join(tmpdir(), "m0-incomplete-"));
  const storage = new LibSQLStore({
    id: "m0-incomplete-test",
    url: `file:${join(directory, "mastra.sqlite")}`,
  });
  await storage.init();
  const dispatchedStages: number[] = [];
  const baseUpdate = {
    contractId: "theme-chokepoint-scoring-v1.4",
    executableContractId: "contract-v1",
    executableContractSha256: "a".repeat(64),
    originalCreatedAt: "2026-08-23T10:00:00+00:00",
    originalUpdatedAt: "2026-08-23T11:00:00+00:00",
  };
  const mastra = new Mastra({
    storage,
    workflows: {
      m0: createM0Workflow({
        projectStage1: async () => ({
          ok: true,
          update: {
            ...baseUpdate,
            pythonStatus: "AWAITING_PRODUCT_CONFIRMATION",
            projection: {
              stage: 1,
              state: "completed",
              inputStatus: null,
              outputStatus: "AWAITING_PRODUCT_CONFIRMATION",
              outcome: "awaiting_confirmation",
              artifactIds: ["anchor-1"],
              completedAt: "2026-08-23T11:00:00+00:00",
            },
          },
        }),
        advancePythonStage: async (_correlation, stage) => {
          dispatchedStages.push(stage);
          const incomplete = stage === 3;
          return {
            ok: true,
            update: {
              ...baseUpdate,
              pythonStatus: incomplete
                ? "INCOMPLETE_BUDGET_EXHAUSTED"
                : "SUPPLY_CHAIN_GRAPH_READY",
              projection: {
                stage,
                state: incomplete ? "incomplete" : "completed",
                inputStatus:
                  stage === 2
                    ? "READY_FOR_SUPPLY_CHAIN"
                    : "SUPPLY_CHAIN_GRAPH_READY",
                outputStatus: incomplete
                  ? "INCOMPLETE_BUDGET_EXHAUSTED"
                  : "SUPPLY_CHAIN_GRAPH_READY",
                outcome: incomplete ? "incomplete" : "completed",
                artifactIds: [`stage-${stage}`],
                completedAt: "2026-08-23T11:00:00+00:00",
              },
            },
          };
        },
      }),
    },
  });
  const workflow = mastra.getWorkflow("m0");
  const correlation = { mastraRunId: "mastra-incomplete", pythonRunId: "python-1" };

  try {
    const run = await workflow.createRun({ runId: correlation.mastraRunId });
    await run.start({ inputData: correlation });
    const result = await run.resume({
      step: "suspend-for-confirmation",
      resumeData: {
        ...correlation,
        confirmation: {
          runId: correlation.pythonRunId,
          confirmedAnchorIds: ["anchor-1"],
          confirmedBy: "owner",
          confirmedAt: "2026-08-23T11:00:00+00:00",
        },
      },
    });

    expect(result).toMatchObject({
      status: "success",
      result: {
        pythonStatus: "INCOMPLETE_BUDGET_EXHAUSTED",
        stageResults: [
          { stage: 1, state: "completed" },
          { stage: 2, state: "completed" },
          { stage: 3, state: "incomplete" },
          { stage: 4, state: "not_entered" },
          { stage: 5, state: "not_entered" },
          { stage: 6, state: "not_entered" },
          { stage: 7, state: "not_entered" },
        ],
      },
    });
    expect(dispatchedStages).toEqual([2, 3]);
  } finally {
    await storage.close();
  }
});
