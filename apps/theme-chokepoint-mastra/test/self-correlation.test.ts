import { expect, it } from "vitest";

import { M0Service } from "../src/service.js";

it("rejects durable Python-run self-correlation mismatch before every Python call", async () => {
  const pythonCalls: string[] = [];
  const service = new M0Service({
    store: {
      async getCorrelation() {
        return {
          mastraRunId: "mastra-1",
          pythonRunId: "python-corrupt",
          workflowId: "theme-chokepoint-m0",
        };
      },
    },
    workflow: {
      async getSnapshot() {
        return {
          mastraRunId: "mastra-1",
          pythonRunId: "python-authoritative",
          workflowId: "theme-chokepoint-m0",
        };
      },
    },
    python: {
      async call(tool: string) {
        pythonCalls.push(tool);
        throw new Error("must not be called");
      },
    },
  });
  const mismatch = {
    ok: false,
    error: {
      code: "RUN_CORRELATION_MISMATCH",
      message: "Run correlation mismatch",
      retryable: false,
    },
  };

  await expect(
    service.getM0Status({ mastraRunId: "mastra-1" }),
  ).resolves.toEqual(mismatch);
  await expect(
    service.resumeM0({
      mastraRunId: "mastra-1",
      actor: "owner",
      selectedAnchorIds: ["anchor-1"],
    }),
  ).resolves.toEqual(mismatch);
  await expect(
    service.getM0Artifacts({ mastraRunId: "mastra-1" }),
  ).resolves.toEqual(mismatch);
  expect(pythonCalls).toEqual([]);
});
