import { expect, it } from "vitest";

import { M0Service } from "../src/service.js";

const correlation = {
  mastraRunId: "mastra-1",
  pythonRunId: "python-1",
  workflowId: "theme-chokepoint-m0",
};

const receipt = {
  runId: "python-1",
  confirmedAnchorIds: ["anchor-2", "anchor-1"],
  confirmedBy: "owner",
  confirmedAt: "2026-08-23T11:00:00+00:00",
};

it("reconstructs a committed confirmation after response loss with zero reconfirm", async () => {
  const calls: string[] = [];
  const persisted: unknown[] = [];
  let completed = 0;
  const service = new M0Service({
    store: {
      async getCorrelation() { return correlation; },
      async getConfirmation() { return null; },
      async putConfirmation(_id: string, value: unknown) {
        persisted.push(value);
        return "stored" as const;
      },
      async claimContinue() { return true; },
      async completeContinue() { completed += 1; },
    },
    workflow: { async getSnapshot() { return correlation; } },
    python: {
      async call(tool: string) {
        calls.push(tool);
        if (tool === "theme_chokepoint_get_run") {
          return {
            ok: true,
            data: {
              schema_version: "theme-chokepoint-mcp.v1",
              run_id: "python-1",
              status: "READY_FOR_SUPPLY_CHAIN",
              next_stage: 2,
              confirmed_anchor_ids: receipt.confirmedAnchorIds,
              confirmed_by: receipt.confirmedBy,
              confirmed_at: receipt.confirmedAt,
            },
          };
        }
        if (tool === "theme_chokepoint_continue") {
          return {
            ok: true,
            data: {
              run_id: "python-1",
              contract_id: "theme-chokepoint-scoring-v1.4",
              executable_contract_id: "contract-v1",
              executable_contract_sha256: "a".repeat(64),
              stages: Array.from({ length: 7 }, (_, index) => ({
                stage: index + 1,
                input_status:
                  index === 0 ? null : "READY_FOR_SUPPLY_CHAIN",
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
        throw new Error(`unexpected tool: ${tool}`);
      },
    },
  });

  const result = await service.resumeM0({
    mastraRunId: "mastra-1",
    actor: "owner",
    selectedAnchorIds: ["anchor-1", "anchor-2"],
  });

  expect(result).toMatchObject({
    ok: true,
    data: {
      mastraRunId: "mastra-1",
      pythonRunId: "python-1",
      status: "SIGNAL_EXPORT_READY",
      confirmation: receipt,
    },
  });
  expect(persisted).toEqual([receipt]);
  expect(completed).toBe(1);
  expect(calls).toEqual([
    "theme_chokepoint_get_run",
    "theme_chokepoint_continue",
  ]);
});
