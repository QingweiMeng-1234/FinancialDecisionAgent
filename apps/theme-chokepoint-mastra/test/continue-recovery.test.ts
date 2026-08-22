import { expect, it } from "vitest";

import { M0Service } from "../src/service.js";

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
            stages: [],
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
