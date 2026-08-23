import { mkdir, mkdtemp } from "node:fs/promises";
import { join } from "node:path";

import { expect, it } from "vitest";

import { M0Service } from "../src/service.js";
import { LibSqlM0StateStore } from "../src/state.js";

const correlation = {
  mastraRunId: "mastra-1",
  pythonRunId: "python-1",
  workflowId: "theme-chokepoint-m0",
};
const receipt = {
  runId: "python-1",
  confirmedAnchorIds: ["anchor-1"],
  confirmedBy: "owner",
  confirmedAt: "2026-08-23T11:00:00+00:00",
};

it("recovers after lost continue response with zero reconfirm and zero Stage replay", async () => {
  let claim = false;
  let pythonStatus = "READY_FOR_SUPPLY_CHAIN";
  const calls: string[] = [];
  const store = {
    async getCorrelation() { return correlation; },
    async getConfirmation() { return receipt; },
    async putConfirmation() { return "same" as const; },
    async claimContinue() {
      if (claim) return false;
      claim = true;
      return true;
    },
    async releaseContinue() { claim = false; },
    async completeContinue() { claim = false; },
  };
  const workflow = { async getSnapshot() { return correlation; } };
  const python = {
    async call(tool: string) {
      calls.push(tool);
      if (tool === "theme_chokepoint_get_run") {
        return {
          ok: true,
          data: {
            schema_version: "theme-chokepoint-mcp.v1",
            run_id: "python-1",
            status: pythonStatus,
            next_stage: pythonStatus === "READY_FOR_SUPPLY_CHAIN" ? 2 : null,
            confirmed_anchor_ids: ["anchor-1"],
            confirmed_by: "owner",
            confirmed_at: receipt.confirmedAt,
          },
        };
      }
      if (tool === "theme_chokepoint_continue") {
        pythonStatus = "SIGNAL_EXPORT_READY";
        throw Object.assign(
          new Error("timeout C:/secret/provider token=raw"),
          { code: "ETIMEDOUT" },
        );
      }
      if (tool === "theme_chokepoint_get_artifacts") {
        return {
          ok: true,
          data: {
            run_id: "python-1",
            contract_id: "theme-chokepoint-scoring-v1.4",
            executable_contract_id: "contract-v1",
            executable_contract_sha256: "a".repeat(64),
            stages: Array.from({ length: 7 }, (_, index) => ({
              stage: index + 1,
              input_status: index === 0 ? null : "READY_FOR_SUPPLY_CHAIN",
              output_status:
                index === 6
                  ? "SIGNAL_EXPORT_READY"
                  : index === 0
                    ? "AWAITING_PRODUCT_CONFIRMATION"
                    : "READY_FOR_SUPPLY_CHAIN",
              outcome: "completed",
              artifact_ids: [],
              completed_at: "2026-08-23T11:00:00+00:00",
            })),
            final_status: "SIGNAL_EXPORT_READY",
            created_at: "2026-08-23T10:00:00+00:00",
            updated_at: "2026-08-23T11:01:00+00:00",
          },
        };
      }
      throw new Error("confirm must never be called");
    },
  };

  const first = new M0Service({ store, workflow, python });
  const input = {
    mastraRunId: "mastra-1",
    actor: "owner",
    selectedAnchorIds: ["anchor-1"],
  };
  const lost = await first.resumeM0(input);
  expect(lost).toEqual({
    ok: false,
    error: {
      code: "MCP_TOOL_FAILURE",
      message: "MCP operation failed",
      retryable: true,
    },
  });
  expect(JSON.stringify(lost)).not.toMatch(/secret|provider|token|timeout/i);

  const reopened = new M0Service({ store, workflow, python });
  await expect(reopened.resumeM0(input)).resolves.toMatchObject({
    ok: true,
    data: {
      status: "SIGNAL_EXPORT_READY",
      confirmation: receipt,
    },
  });
  expect(calls.filter((tool) => tool === "theme_chokepoint_confirm_anchors")).toHaveLength(0);
  expect(calls.filter((tool) => tool === "theme_chokepoint_continue")).toHaveLength(1);
});

it("takes over an expired pre-commit claim with zero reconfirm and one workflow dispatch", async () => {
  const tempRoot = join(process.cwd(), ".tmp");
  await mkdir(tempRoot, { recursive: true });
  const directory = await mkdtemp(join(tempRoot, "m0-crash-takeover-"));
  const store = await LibSqlM0StateStore.open(join(directory, "state.sqlite"));
  await store.createCorrelation(correlation);
  await store.putConfirmation("mastra-1", receipt);
  type LeaseStore = {
    claimContinue(
      runId: string,
      owner: string,
      nowMs: number,
      leaseMs: number,
    ): Promise<boolean>;
  };
  await (store as unknown as LeaseStore).claimContinue(
    "mastra-1",
    "dead-process",
    1,
    1,
  );
  let pythonStatus = "READY_FOR_SUPPLY_CHAIN";
  const pythonCalls: string[] = [];
  const workflowDispatches: string[] = [];
  const manifest = {
    run_id: "python-1",
    contract_id: "theme-chokepoint-scoring-v1.4",
    executable_contract_id: "contract-v1",
    executable_contract_sha256: "a".repeat(64),
    stages: [
      {
        stage: 1,
        input_status: null,
        output_status: "AWAITING_PRODUCT_CONFIRMATION",
        outcome: "awaiting_confirmation",
        artifact_ids: [],
        completed_at: "2026-08-23T10:00:00+00:00",
      },
      ...Array.from({ length: 6 }, (_, index) => ({
        stage: index + 2,
        input_status: "READY_FOR_SUPPLY_CHAIN",
        output_status:
          index === 5 ? "SIGNAL_EXPORT_READY" : "READY_FOR_SUPPLY_CHAIN",
        outcome: "completed",
        artifact_ids: [],
        completed_at: "2026-08-23T11:00:00+00:00",
      })),
    ],
    final_status: "SIGNAL_EXPORT_READY",
    created_at: "2026-08-23T10:00:00+00:00",
    updated_at: "2026-08-23T11:01:00+00:00",
  };
  const workflow = {
    async getSnapshot() { return correlation; },
    async resume() {
      workflowDispatches.push("continue");
      pythonStatus = "SIGNAL_EXPORT_READY";
      return {
        status: "success" as const,
        result: {
          mastraRunId: "mastra-1",
          pythonRunId: "python-1",
          confirmation: receipt,
          manifest,
        },
      };
    },
  };
  const python = {
    async call(tool: string) {
      pythonCalls.push(tool);
      if (tool === "theme_chokepoint_get_run") {
        return {
          ok: true,
          data: {
            schema_version: "theme-chokepoint-mcp.v1",
            run_id: "python-1",
            status: pythonStatus,
            next_stage: pythonStatus === "READY_FOR_SUPPLY_CHAIN" ? 2 : null,
            confirmed_anchor_ids: ["anchor-1"],
            confirmed_by: "owner",
            confirmed_at: receipt.confirmedAt,
          },
        };
      }
      if (tool === "theme_chokepoint_get_artifacts") {
        return { ok: true, data: manifest };
      }
      throw new Error(`unexpected direct Python dispatch: ${tool}`);
    },
  };
  const input = {
    mastraRunId: "mastra-1",
    actor: "owner",
    selectedAnchorIds: ["anchor-1"],
  };

  try {
    await expect(
      new M0Service({ store, workflow, python }).resumeM0(input),
    ).resolves.toMatchObject({
      ok: true,
      data: { status: "SIGNAL_EXPORT_READY", confirmation: receipt },
    });
    await expect(
      new M0Service({ store, workflow, python }).resumeM0(input),
    ).resolves.toMatchObject({
      ok: true,
      data: { status: "SIGNAL_EXPORT_READY", confirmation: receipt },
    });
    expect(workflowDispatches).toEqual(["continue"]);
    expect(pythonCalls).toEqual([
      "theme_chokepoint_get_run",
      "theme_chokepoint_get_run",
      "theme_chokepoint_get_artifacts",
      "theme_chokepoint_get_run",
      "theme_chokepoint_get_artifacts",
    ]);
  } finally {
    await store.close();
  }
});
