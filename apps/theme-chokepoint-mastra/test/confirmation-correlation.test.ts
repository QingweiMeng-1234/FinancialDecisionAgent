import { expect, it } from "vitest";

import { M0Service } from "../src/service.js";

const correlation = {
  mastraRunId: "mastra-1",
  pythonRunId: "python-1",
  workflowId: "theme-chokepoint-m0",
};

const awaiting = {
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

const pending = {
  ok: true,
  data: {
    schema_version: "theme-chokepoint-mcp.v1",
    run_id: "python-1",
    status: "AWAITING_PRODUCT_CONFIRMATION",
    anchors: [
      {
        anchor_id: "anchor-1",
        product_name: "transformer",
        buyer_or_user: "operator",
        demand_variable: "MW",
        theme_link: "power",
        confidence: 0.9,
        supporting_evidence_ids: ["ev-1"],
        missing_evidence: [],
        status: "proposed",
      },
    ],
  },
};

it("rejects every direct-confirm run/actor/ID substitution with zero continue", async () => {
  const variants = [
    { run_id: "python-other", confirmed_by: "owner", confirmed_anchor_ids: ["anchor-1"] },
    { run_id: "python-1", confirmed_by: "other", confirmed_anchor_ids: ["anchor-1"] },
    { run_id: "python-1", confirmed_by: "owner", confirmed_anchor_ids: ["anchor-other"] },
    { run_id: "python-1", confirmed_by: "owner", confirmed_anchor_ids: ["anchor-1", "anchor-1"] },
  ];

  for (const variant of variants) {
    const calls: string[] = [];
    let persisted = 0;
    const service = new M0Service({
      store: {
        async getCorrelation() { return correlation; },
        async getConfirmation() { return null; },
        async putConfirmation() { persisted += 1; return "stored" as const; },
        async claimContinue() { throw new Error("continue must not be claimed"); },
      },
      workflow: { async getSnapshot() { return correlation; } },
      python: {
        async call(tool: string) {
          calls.push(tool);
          if (tool === "theme_chokepoint_get_run") return awaiting;
          if (tool === "theme_chokepoint_get_pending_anchors") return pending;
          if (tool === "theme_chokepoint_confirm_anchors") {
            return {
              ok: true,
              data: {
                schema_version: "theme-chokepoint-mcp.v1",
                ...variant,
                status: "READY_FOR_SUPPLY_CHAIN",
                confirmed_at: "2026-08-23T11:00:00+00:00",
              },
            };
          }
          throw new Error("continue must not be called");
        },
      },
    });

    await expect(
      service.resumeM0({
        mastraRunId: "mastra-1",
        actor: "owner",
        selectedAnchorIds: ["anchor-1"],
      }),
    ).resolves.toEqual({
      ok: false,
      error: {
        code: "MCP_RESPONSE_SCHEMA_MISMATCH",
        message: "MCP response schema mismatch",
        retryable: false,
      },
    });
    expect(persisted).toBe(0);
    expect(calls).toEqual([
      "theme_chokepoint_get_run",
      "theme_chokepoint_get_pending_anchors",
      "theme_chokepoint_confirm_anchors",
    ]);
  }
});
