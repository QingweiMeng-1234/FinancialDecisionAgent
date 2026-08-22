import { expect, it } from "vitest";

import { MastraMcpPythonClient } from "../src/mcp-client.js";

it("maps a real pinned-client loopback outage to a fixed retryable redacted failure", async () => {
  const diagnostics: unknown[] = [];
  const client = new MastraMcpPythonClient({
    url: "http://127.0.0.1:1/mcp",
    timeoutMs: 250,
    logger(event: unknown) {
      diagnostics.push(event);
    },
  });

  let captured: unknown;
  try {
    await client.call("theme_chokepoint_get_run", { run_id: "python-1" });
  } catch (error) {
    captured = error;
  } finally {
    await client.close();
  }

  expect(captured).toMatchObject({
    code: "MCP_TOOL_FAILURE",
    message: "MCP operation failed",
    retryable: true,
  });
  const serialized = JSON.stringify({ captured, diagnostics });
  expect(serialized).not.toMatch(
    /node_modules|provider|127\.0\.0\.1|http:|token|[A-Z]:\\/i,
  );
  expect(diagnostics).toEqual([
    {
      tool: "theme_chokepoint_get_run",
      code: "MCP_TOOL_FAILURE",
      retryable: true,
    },
  ]);
});
