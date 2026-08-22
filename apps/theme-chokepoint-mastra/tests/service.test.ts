import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  createM0Service as createService,
  type ThemeChokepointMcp,
} from "../src/service.js";

const roots: string[] = [];
const services: Awaited<ReturnType<typeof createService>>[] = [];

async function createM0Service(
  options: Parameters<typeof createService>[0],
) {
  const service = await createService(options);
  services.push(service);
  return service;
}

async function storageUrl(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "tc-m0-"));
  roots.push(root);
  return `file:${join(root, "mastra.db")}`;
}

afterEach(async () => {
  await Promise.all(services.splice(0).map((service) => service.close()));
  roots.length = 0;
});

const anchor = {
  anchor_id: "anchor-1",
  product_name: "Transformers",
  buyer_or_user: "Utilities",
  demand_variable: "unit demand",
  theme_link: "Grid upgrades",
  confidence: 0.8,
  supporting_evidence_ids: ["evidence-1"],
  missing_evidence: [],
  status: "proposed",
};

const request = {
  theme: "AI datacenter power",
  trigger: "datacenter buildout",
  region: "global",
  asOfDate: "2026-08-22",
  timeHorizonMonths: 24,
  analysisGoal: "find physical chokepoints",
  seedProducts: [],
  seedCompanies: [],
  researchMode: "assisted" as const,
  maxDepth: 4,
  maxNodes: 50,
  maxIterations: 3,
  maxSources: 30,
  maxTimeSeconds: 900,
  maxCostUsd: 10,
  maxProductAnchors: 5,
};

class FakeMcp implements ThemeChokepointMcp {
  status = "AWAITING_PRODUCT_CONFIRMATION";
  anchors = [anchor];
  confirmCalls = 0;
  continueCalls = 0;
  confirmedBy: string | null = null;

  async start(input: Record<string, unknown>) {
    return ok({
      schema_version: "theme-chokepoint-mcp.v1",
      run_id: input.run_id,
      status: this.status,
      next_stage: null,
    });
  }

  async getRun(runId: string) {
    return ok({
      schema_version: "theme-chokepoint-mcp.v1",
      run_id: runId,
      status: this.status,
      next_stage: null,
      confirmed_by: this.confirmedBy,
      confirmed_at: this.confirmedBy ? "2026-08-22T12:00:00+00:00" : null,
    });
  }

  async getPendingAnchors(runId: string) {
    return ok({
      schema_version: "theme-chokepoint-mcp.v1",
      run_id: runId,
      status: this.status,
      demand_frame: {},
      anchors: this.anchors,
    });
  }

  async confirmAnchors(runId: string, ids: string[], actor: string) {
    if (this.status !== "AWAITING_PRODUCT_CONFIRMATION") {
      return fail("RUN_NOT_AWAITING_CONFIRMATION");
    }
    this.status = "READY_FOR_SUPPLY_CHAIN";
    this.confirmCalls += 1;
    this.confirmedBy = actor;
    return ok({
      schema_version: "theme-chokepoint-mcp.v1",
      run_id: runId,
      status: this.status,
      confirmed_anchor_ids: ids,
      confirmed_by: actor,
      confirmed_at: "2026-08-22T12:00:00+00:00",
    });
  }

  async continueRun(runId: string) {
    this.continueCalls += 1;
    this.status = "SIGNAL_EXPORT_READY";
    return ok({
      schema_version: "theme-chokepoint-mcp.v1",
      run_id: runId,
      status: this.status,
      stages: [],
    });
  }

  async getArtifacts(runId: string) {
    return ok({
      schema_version: "theme-chokepoint-mcp.v1",
      run_id: runId,
      status: this.status,
      stages: [{
        stage: 1,
        input_status: null,
        output_status: "AWAITING_PRODUCT_CONFIRMATION",
        outcome: "awaiting_human_confirmation",
        artifact_ids: [runId],
        completed_at: "2026-08-22T11:00:00+00:00",
      }],
    });
  }
}

function ok(data: Record<string, unknown>) {
  return { ok: true as const, data };
}

function fail(code: string) {
  return {
    ok: false as const,
    error: { code, message: "safe failure", retryable: false },
  };
}

describe("Theme Chokepoint Mastra M0", () => {
  it("uses the standard workflow gate and preserves Python anchor order", async () => {
    const mcp = new FakeMcp();
    const service = await createM0Service({ mcp, storageUrl: await storageUrl() });

    const result = await service.startM0(request);

    expect(result.workflowStatus).toBe("suspended");
    expect(result.pythonStatus).toBe("AWAITING_PRODUCT_CONFIRMATION");
    expect(result.resumable).toBe(true);
    expect(result.anchors).toEqual([anchor]);
    expect(service.workflow.id).toBe("theme-chokepoint-m0");
    expect(service.suspendStepId).toBe("await-product-confirmation");
  });

  it("fails closed before continue unless the correlated confirmation succeeds", async () => {
    const mcp = new FakeMcp();
    const service = await createM0Service({ mcp, storageUrl: await storageUrl() });
    const started = await service.startM0(request);

    await expect(service.resumeM0({
      mastraRunId: started.mastraRunId,
      actor: "analyst",
      selectedAnchorIds: ["foreign"],
    })).rejects.toMatchObject({ code: "ANCHOR_NOT_PROPOSED_FOR_RUN" });
    expect(mcp.confirmCalls).toBe(0);
    expect(mcp.continueCalls).toBe(0);

    const resumed = await service.resumeM0({
      mastraRunId: started.mastraRunId,
      actor: " analyst ",
      selectedAnchorIds: ["anchor-1"],
    });
    expect(resumed.pythonStatus).toBe("SIGNAL_EXPORT_READY");
    expect(mcp.confirmCalls).toBe(1);
    expect(mcp.continueCalls).toBe(1);
  });

  it("persists correlation and resumes the same run after runtime reopen", async () => {
    const url = await storageUrl();
    const mcp = new FakeMcp();
    const first = await createM0Service({ mcp, storageUrl: url });
    const started = await first.startM0(request);
    await first.close();

    const reopened = await createM0Service({ mcp, storageUrl: url });
    const status = await reopened.getM0Status(started.mastraRunId);
    expect(status.pythonRunId).toBe(started.pythonRunId);
    expect(status.resumable).toBe(true);

    await reopened.resumeM0({
      mastraRunId: started.mastraRunId,
      actor: "analyst",
      selectedAnchorIds: ["anchor-1"],
    });
    expect(mcp.confirmCalls).toBe(1);
  });

  it("allows at most one concurrent or duplicate confirmation", async () => {
    const mcp = new FakeMcp();
    const service = await createM0Service({ mcp, storageUrl: await storageUrl() });
    const started = await service.startM0(request);
    const resume = () => service.resumeM0({
      mastraRunId: started.mastraRunId,
      actor: "analyst",
      selectedAnchorIds: ["anchor-1"],
    });

    const settled = await Promise.allSettled([resume(), resume()]);

    expect(settled.filter((item) => item.status === "fulfilled")).toHaveLength(1);
    expect(mcp.confirmCalls).toBe(1);
    expect(mcp.continueCalls).toBe(1);
    await expect(resume()).rejects.toMatchObject({
      code: "CONCURRENT_OR_DUPLICATE_RESUME",
    });
  });

  it("never continues after a mismatched confirmation receipt", async () => {
    for (const mismatch of ["run", "status"] as const) {
      const mcp = new FakeMcp();
      mcp.confirmAnchors = async (runId, ids, actor) => ok({
        schema_version: "theme-chokepoint-mcp.v1",
        run_id: mismatch === "run" ? "different-python-run" : runId,
        status: mismatch === "status"
          ? "AWAITING_PRODUCT_CONFIRMATION"
          : "READY_FOR_SUPPLY_CHAIN",
        confirmed_anchor_ids: ids,
        confirmed_by: actor,
        confirmed_at: "2026-08-22T12:00:00+00:00",
      });
      const service = await createM0Service({
        mcp,
        storageUrl: await storageUrl(),
      });
      const started = await service.startM0(request);

      await expect(service.resumeM0({
        mastraRunId: started.mastraRunId,
        actor: "analyst",
        selectedAnchorIds: ["anchor-1"],
      })).rejects.toMatchObject({
        code: mismatch === "run"
          ? "RUN_CORRELATION_MISMATCH"
          : "PYTHON_STATUS_MISMATCH",
      });
      expect(mcp.continueCalls).toBe(0);
    }
  });

  it("rejects strict-input violations, non-finite values, unknown state, and unsafe URLs", async () => {
    await expect(createM0Service({
      mcp: new FakeMcp(),
      storageUrl: await storageUrl(),
      mcpUrl: "http://example.com:8877/mcp",
    })).rejects.toMatchObject({ code: "INVALID_ARGUMENT" });

    const mcp = new FakeMcp();
    const service = await createM0Service({ mcp, storageUrl: await storageUrl() });
    await expect(service.startM0({ ...request, maxCostUsd: Number.NaN }))
      .rejects.toMatchObject({ code: "INVALID_ARGUMENT" });
    await expect(service.startM0({ ...request, extra: true } as never))
      .rejects.toMatchObject({ code: "INVALID_ARGUMENT" });

    mcp.status = "NOT_A_REAL_STATUS";
    await expect(service.startM0(request))
      .rejects.toMatchObject({ code: "UNKNOWN_PYTHON_STATUS" });
  });

  it("redacts malformed MCP responses and reads only root-manifest artifacts", async () => {
    const mcp = new FakeMcp();
    mcp.getRun = async () => (
      "{malformed token=secret localPath=C:/private" as never
    );
    const service = await createM0Service({ mcp, storageUrl: await storageUrl() });

    await expect(service.startM0(request)).rejects.toSatisfy((error: unknown) => {
      const serialized = JSON.stringify(error);
      return serialized.includes("MCP_RESPONSE_SCHEMA_MISMATCH")
        && !serialized.includes("secret")
        && !serialized.includes("C:/private");
    });

    const throwingMcp = new FakeMcp();
    throwingMcp.getRun = async () => {
      throw new Error("token=secret provider-body C:/private");
    };
    const throwing = await createM0Service({
      mcp: throwingMcp,
      storageUrl: await storageUrl(),
    });
    await expect(throwing.startM0(request)).rejects.toSatisfy((error: unknown) => {
      const serialized = JSON.stringify(error);
      return serialized.includes("MCP_TOOL_FAILURE")
        && !serialized.includes("secret")
        && !serialized.includes("C:/private");
    });

    const healthyMcp = new FakeMcp();
    const healthy = await createM0Service({
      mcp: healthyMcp,
      storageUrl: await storageUrl(),
    });
    const started = await healthy.startM0(request);
    const artifacts = await healthy.getM0Artifacts(started.mastraRunId);
    expect(artifacts.stages[0]?.artifactIds).toEqual([started.pythonRunId]);
  });
});
