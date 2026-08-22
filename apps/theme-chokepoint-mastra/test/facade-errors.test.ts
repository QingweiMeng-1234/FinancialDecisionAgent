import { describe, expect, it } from "vitest";

import { M0Service } from "../src/service.js";

describe("public facade failure envelopes", () => {
  it("returns INVALID_ARGUMENT instead of rejecting for every public method", async () => {
    const service = new M0Service();
    const expected = {
      ok: false,
      error: {
        code: "INVALID_ARGUMENT",
        message: "Invalid request",
        retryable: false,
      },
    };

    await expect(service.startM0({})).resolves.toEqual(expected);
    await expect(service.resumeM0({})).resolves.toEqual(expected);
    await expect(service.getM0Status({})).resolves.toEqual(expected);
    await expect(service.getM0Artifacts({})).resolves.toEqual(expected);
  });

  it.each(["persist confirmation", "claim continue"])(
    "classifies %s storage failures without leaking diagnostics",
    async (operation) => {
      const correlation = {
        mastraRunId: "mastra-1",
        pythonRunId: "python-1",
        workflowId: "theme-chokepoint-m0",
      };
      const storageError = Object.assign(
        new Error("C:/secret/state.sqlite token=raw"),
        { code: "SQLITE_BUSY" },
      );
      const store = {
        async getCorrelation() { return correlation; },
        async putConfirmation() {
          if (operation === "persist confirmation") throw storageError;
          return "same" as const;
        },
        async claimContinue() {
          if (operation === "claim continue") throw storageError;
          return true;
        },
        async releaseContinue() {},
        async completeContinue() {},
      };
      const workflow = { async getSnapshot() { return correlation; } };
      const python = {
        async call(tool: string) {
          if (tool === "theme_chokepoint_get_run") {
            return {
              ok: true,
              data: {
                schema_version: "theme-chokepoint-mcp.v1",
                run_id: "python-1",
                status: "READY_FOR_SUPPLY_CHAIN",
                next_stage: 2,
                confirmed_anchor_ids: ["anchor-1"],
                confirmed_by: "owner",
                confirmed_at: "2026-08-23T11:00:00+00:00",
              },
            };
          }
          throw new Error("continue must not be reached");
        },
      };
      const result = await new M0Service({ store, workflow, python }).resumeM0({
        mastraRunId: "mastra-1",
        actor: "owner",
        selectedAnchorIds: ["anchor-1"],
      });

      expect(result).toEqual({
        ok: false,
        error: {
          code: "STORAGE_FAILURE",
          message: "Persistent storage operation failed",
          retryable: true,
        },
      });
      expect(JSON.stringify(result)).not.toMatch(/secret|sqlite|token|raw/i);
    },
  );
});
