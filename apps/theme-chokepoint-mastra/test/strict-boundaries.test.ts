import { expect, it } from "vitest";

import { M0Service } from "../src/service.js";

const correlation = {
  mastraRunId: "mastra-1",
  pythonRunId: "python-1",
  workflowId: "theme-chokepoint-m0",
};

function ports(call: (tool: string) => Promise<unknown>) {
  return {
    store: { async getCorrelation() { return correlation; } },
    workflow: { async getSnapshot() { return correlation; } },
    python: { call },
  };
}

function runData(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "theme-chokepoint-mcp.v1",
    run_id: "python-1",
    status: "READY_FOR_SUPPLY_CHAIN",
    next_stage: 2,
    confirmed_anchor_ids: ["anchor-1"],
    confirmed_by: "owner",
    confirmed_at: "2026-08-23T11:00:00+00:00",
    ...overrides,
  };
}

it("fails closed on malformed, extra, uncorrelated, and unknown status success payloads", async () => {
  const schemaMismatch = {
    ok: false,
    error: {
      code: "MCP_RESPONSE_SCHEMA_MISMATCH",
      message: "MCP response schema mismatch",
      retryable: false,
    },
  };
  const extra = new M0Service(
    ports(async () => ({
      ok: true,
      data: runData({ provider_token: "secret" }),
    })),
  );
  await expect(
    extra.getM0Status({ mastraRunId: "mastra-1" }),
  ).resolves.toEqual(schemaMismatch);

  const wrongRun = new M0Service(
    ports(async () => ({
      ok: true,
      data: runData({ run_id: "python-other" }),
    })),
  );
  await expect(
    wrongRun.getM0Status({ mastraRunId: "mastra-1" }),
  ).resolves.toEqual(schemaMismatch);

  const unknown = new M0Service(
    ports(async () => ({
      ok: true,
      data: runData({ status: "PROVIDER_SECRET_STATUS" }),
    })),
  );
  await expect(
    unknown.getM0Status({ mastraRunId: "mastra-1" }),
  ).resolves.toEqual({
    ok: false,
    error: {
      code: "UNKNOWN_PYTHON_STATUS",
      message: "Python returned an unknown run status",
      retryable: false,
    },
  });
});

it("validates artifact success strictly after a correlated authoritative status read", async () => {
  const calls: string[] = [];
  const service = new M0Service(
    ports(async (tool) => {
      calls.push(tool);
      if (tool === "theme_chokepoint_get_run") {
        return {
          ok: true,
          data: runData({ status: "SUPPLY_CHAIN_GRAPH_READY", next_stage: 3 }),
        };
      }
      return {
        ok: true,
        data: {
          run_id: "python-1",
          final_status: "SUPPLY_CHAIN_GRAPH_READY",
          local_path: "C:/secret/provider/output.json",
        },
      };
    }),
  );

  await expect(
    service.getM0Artifacts({ mastraRunId: "mastra-1" }),
  ).resolves.toEqual({
    ok: false,
    error: {
      code: "MCP_RESPONSE_SCHEMA_MISMATCH",
      message: "MCP response schema mismatch",
      retryable: false,
    },
  });
  expect(calls).toEqual([
    "theme_chokepoint_get_run",
    "theme_chokepoint_get_artifacts",
  ]);
});
