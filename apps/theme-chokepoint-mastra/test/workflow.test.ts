import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { Mastra } from "@mastra/core";
import { LibSQLStore } from "@mastra/libsql";
import { expect, it } from "vitest";

import { createM0Workflow } from "../src/workflow.js";

it("uses a fixed Mastra workflow graph that only routes to the Python authority", async () => {
  const workflow = createM0Workflow() as {
    id: string;
    steps: Record<string, { id: string }>;
  };

  expect(workflow.id).toBe("theme-chokepoint-m0");
  expect(Object.keys(workflow.steps).sort()).toEqual([
    "continue-python-run",
    "correlate-python-run",
    "suspend-for-confirmation",
  ]);
  const source = await readFile(
    new URL("../src/workflow.ts", import.meta.url),
    "utf8",
  );
  expect(source).not.toMatch(/sqlite|stage[2-7]|transition[_ -]?table/i);
  expect(source).toMatch(/createWorkflow|createStep/);
});

it("suspends before confirmation and resumes only with correlated receipt data", async () => {
  const directory = await mkdtemp(join(tmpdir(), "m0-workflow-"));
  const storage = new LibSQLStore({
    id: "m0-workflow-test",
    url: `file:${join(directory, "mastra.sqlite")}`,
  });
  await storage.init();
  const mastra = new Mastra({
    storage,
    workflows: { m0: createM0Workflow() },
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
      result: correlation,
    });
  } finally {
    await storage.close();
  }
});
