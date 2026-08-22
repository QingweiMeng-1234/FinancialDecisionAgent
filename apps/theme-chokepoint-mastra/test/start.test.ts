import { expect, it } from "vitest";

import { M0Service } from "../src/service.js";

it("persists correlation only after a strict correlated Python start response", async () => {
  const correlations: unknown[] = [];
  const snapshots: unknown[] = [];
  const service = new M0Service({
    store: {
      async getCorrelation() { return null; },
      async createCorrelation(value: unknown) { correlations.push(value); },
    },
    workflow: {
      async getSnapshot() { return null; },
      async createSnapshot(value: unknown) { snapshots.push(value); },
    },
    python: {
      async call(tool: string) {
        expect(tool).toBe("theme_chokepoint_start");
        return {
          ok: true,
          data: {
            schema_version: "theme-chokepoint-mcp.v1",
            run_id: "python-1",
            status: "AWAITING_PRODUCT_CONFIRMATION",
            next_stage: null,
            confirmed_anchor_ids: [],
            confirmed_by: null,
            confirmed_at: null,
          },
        };
      },
    },
  });

  await expect(
    service.startM0({
      mastraRunId: "mastra-1",
      pythonRequest: { run_id: "python-1", theme: "power" },
    }),
  ).resolves.toMatchObject({
    ok: true,
    data: {
      mastraRunId: "mastra-1",
      pythonRunId: "python-1",
      status: "AWAITING_PRODUCT_CONFIRMATION",
    },
  });
  const expected = {
    mastraRunId: "mastra-1",
    pythonRunId: "python-1",
    workflowId: "theme-chokepoint-m0",
  };
  expect(correlations).toEqual([expected]);
  expect(snapshots).toEqual([expected]);
});

it("rejects an uncorrelated start success before any durable write", async () => {
  let writes = 0;
  const service = new M0Service({
    store: {
      async getCorrelation() { return null; },
      async createCorrelation() { writes += 1; },
    },
    workflow: {
      async getSnapshot() { return null; },
      async createSnapshot() { writes += 1; },
    },
    python: {
      async call() {
        return {
          ok: true,
          data: {
            schema_version: "theme-chokepoint-mcp.v1",
            run_id: "other-run",
            status: "AWAITING_PRODUCT_CONFIRMATION",
            next_stage: null,
            confirmed_anchor_ids: [],
            confirmed_by: null,
            confirmed_at: null,
          },
        };
      },
    },
  });

  await expect(
    service.startM0({
      mastraRunId: "mastra-1",
      pythonRequest: { run_id: "python-1", theme: "power" },
    }),
  ).resolves.toEqual({
    ok: false,
    error: {
      code: "MCP_RESPONSE_SCHEMA_MISMATCH",
      message: "MCP response schema mismatch",
      retryable: false,
    },
  });
  expect(writes).toBe(0);
});
