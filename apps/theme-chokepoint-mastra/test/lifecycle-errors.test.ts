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

function run(status: string) {
  return {
    ok: true,
    data: {
      schema_version: "theme-chokepoint-mcp.v1",
      run_id: "python-1",
      status,
      next_stage: status === "READY_FOR_SUPPLY_CHAIN" ? 2 : null,
      confirmed_anchor_ids:
        status === "AWAITING_PRODUCT_CONFIRMATION" ? [] : ["anchor-1"],
      confirmed_by:
        status === "AWAITING_PRODUCT_CONFIRMATION" ? null : "owner",
      confirmed_at:
        status === "AWAITING_PRODUCT_CONFIRMATION"
          ? null
          : receipt.confirmedAt,
    },
  };
}

function knownFailure(code: string, message: string) {
  return { ok: false, error: { code, message, retryable: false } };
}

function ports(call: (tool: string) => Promise<unknown>) {
  return {
    store: {
      async getCorrelation() { return correlation; },
      async putConfirmation() { return "same" as const; },
      async claimContinue() { return true; },
      async releaseContinue() {},
      async completeContinue() {},
    },
    workflow: { async getSnapshot() { return correlation; } },
    python: { call },
  };
}

it("preserves the stable lifecycle error-code matrix at every Python boundary", async () => {
  const duplicate = await new M0Service(
    ports(async () => { throw new Error("Python must not be called"); }),
  ).resumeM0({
    mastraRunId: "mastra-1",
    actor: "owner",
    selectedAnchorIds: ["anchor-1", "anchor-1"],
  });

  const pendingFailure = knownFailure(
    "RUN_NOT_AWAITING_CONFIRMATION",
    "Run is not awaiting confirmation",
  );
  const pending = await new M0Service(
    ports(async (tool) =>
      tool === "theme_chokepoint_get_run"
        ? run("AWAITING_PRODUCT_CONFIRMATION")
        : pendingFailure,
    ),
  ).resumeM0({
    mastraRunId: "mastra-1",
    actor: "owner",
    selectedAnchorIds: ["anchor-1"],
  });

  const confirmFailure = knownFailure(
    "ANCHOR_NOT_PROPOSED_FOR_RUN",
    "Anchor is not proposed for this run",
  );
  const confirm = await new M0Service(
    ports(async (tool) => {
      if (tool === "theme_chokepoint_get_run") {
        return run("AWAITING_PRODUCT_CONFIRMATION");
      }
      if (tool === "theme_chokepoint_get_pending_anchors") {
        return {
          ok: true,
          data: {
            schema_version: "theme-chokepoint-mcp.v1",
            run_id: "python-1",
            status: "AWAITING_PRODUCT_CONFIRMATION",
            anchors: [{
              anchor_id: "anchor-1",
              product_name: "UPS",
              buyer_or_user: "operator",
              demand_variable: "MW",
              theme_link: "power",
              confidence: 0.9,
              supporting_evidence_ids: [],
              missing_evidence: [],
              status: "proposed",
            }],
          },
        };
      }
      return confirmFailure;
    }),
  ).resumeM0({
    mastraRunId: "mastra-1",
    actor: "owner",
    selectedAnchorIds: ["anchor-1"],
  });

  const continueFailure = knownFailure(
    "RUN_NOT_AWAITING_CONFIRMATION",
    "Run is not awaiting confirmation",
  );
  const continued = await new M0Service(
    ports(async (tool) =>
      tool === "theme_chokepoint_get_run"
        ? run("READY_FOR_SUPPLY_CHAIN")
        : continueFailure,
    ),
  ).resumeM0({
    mastraRunId: "mastra-1",
    actor: "owner",
    selectedAnchorIds: ["anchor-1"],
  });

  const artifactFailure = knownFailure("RUN_NOT_FOUND", "Run was not found");
  const artifact = await new M0Service(
    ports(async (tool) =>
      tool === "theme_chokepoint_get_run"
        ? run("SIGNAL_EXPORT_READY")
        : artifactFailure,
    ),
  ).getM0Artifacts({ mastraRunId: "mastra-1" });

  expect([duplicate, pending, confirm, continued, artifact]).toEqual([
    knownFailure("DUPLICATE_ANCHOR_ID", "Anchor IDs must be unique"),
    pendingFailure,
    confirmFailure,
    continueFailure,
    artifactFailure,
  ]);
});
