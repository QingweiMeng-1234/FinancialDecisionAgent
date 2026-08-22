import { expect, it } from "vitest";

import { M0Service } from "../src/service.js";

const correlation = {
  mastraRunId: "mastra-1",
  pythonRunId: "python-1",
  workflowId: "theme-chokepoint-m0",
};

function workflow() {
  return { async getSnapshot() { return correlation; } };
}

it("normalizes known and unknown storage failures without leaking diagnostics", async () => {
  for (const [error, retryable] of [
    [Object.assign(new Error("C:/secret/libsql.db token=raw"), { code: "SQLITE_BUSY" }), true],
    [Object.assign(new Error("provider corruption C:/secret"), { code: "DRIVER_PRIVATE" }), false],
  ] as const) {
    const service = new M0Service({
      store: { async getCorrelation() { throw error; } },
      workflow: workflow(),
      python: { async call() { throw new Error("must not be called"); } },
    });
    const result = await service.getM0Status({ mastraRunId: "mastra-1" });
    expect(result).toEqual({
      ok: false,
      error: {
        code: "STORAGE_FAILURE",
        message: "Persistent storage operation failed",
        retryable,
      },
    });
    expect(JSON.stringify(result)).not.toMatch(/secret|provider|token|libsql|driver/i);
  }
});

it("normalizes MCP exceptions and unknown Python codes with fixed redacted output", async () => {
  for (const [python, retryable] of [
    [
      { async call() {
        throw Object.assign(new Error("http://localhost/token/provider"), {
          code: "ECONNREFUSED",
        });
      } },
      true,
    ],
    [
      { async call() {
        return {
          ok: false,
          error: {
            code: "RAW_PROVIDER_FAILURE",
            message: "C:/secret/token",
            retryable: true,
          },
        };
      } },
      false,
    ],
  ] as const) {
    const service = new M0Service({
      store: { async getCorrelation() { return correlation; } },
      workflow: workflow(),
      python,
    });
    const result = await service.getM0Status({ mastraRunId: "mastra-1" });
    expect(result).toEqual({
      ok: false,
      error: {
        code: "MCP_TOOL_FAILURE",
        message: "MCP operation failed",
        retryable,
      },
    });
    expect(JSON.stringify(result)).not.toMatch(/secret|provider|token|localhost|http/i);
  }
});

it("preserves a known Python RUN_NOT_FOUND code but replaces its message", async () => {
  const service = new M0Service({
    store: { async getCorrelation() { return correlation; } },
    workflow: workflow(),
    python: {
      async call() {
        return {
          ok: false,
          error: {
            code: "RUN_NOT_FOUND",
            message: "raw C:/secret/run.db",
            retryable: false,
          },
        };
      },
    },
  });
  await expect(
    service.getM0Status({ mastraRunId: "mastra-1" }),
  ).resolves.toEqual({
    ok: false,
    error: {
      code: "RUN_NOT_FOUND",
      message: "Run was not found",
      retryable: false,
    },
  });
});
