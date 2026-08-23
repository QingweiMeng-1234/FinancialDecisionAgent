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
