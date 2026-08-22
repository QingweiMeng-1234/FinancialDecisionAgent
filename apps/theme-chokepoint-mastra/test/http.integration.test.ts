import { spawn } from "node:child_process";
import { createServer, createConnection } from "node:net";
import { resolve } from "node:path";

import { expect, it } from "vitest";

import { MastraMcpPythonClient } from "../src/mcp-client.js";

async function freePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>((resolveReady) =>
    server.listen(0, "127.0.0.1", resolveReady),
  );
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>((resolveClosed) => server.close(() => resolveClosed()));
  return port;
}

async function waitForPort(port: number): Promise<void> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const ready = await new Promise<boolean>((resolveReady) => {
      const socket = createConnection({ host: "127.0.0.1", port });
      socket.once("connect", () => {
        socket.destroy();
        resolveReady(true);
      });
      socket.once("error", () => resolveReady(false));
    });
    if (ready) return;
    await new Promise((resolveWait) => setTimeout(resolveWait, 500));
  }
  throw new Error("Python MCP server did not become ready");
}

it("calls the production Python wrapper over real local Streamable HTTP", async () => {
  const port = await freePort();
  const repositoryRoot = resolve(process.cwd(), "../..");
  const child = spawn(
    "python",
    [
      resolve(process.cwd(), "test/support/python_http_server.py"),
      "--port",
      String(port),
    ],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        PYTHONPATH: [resolve(repositoryRoot, "src"), resolve(repositoryRoot, "tests")].join(";"),
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  let output = "";
  child.stdout.on("data", (chunk) => { output += String(chunk); });
  child.stderr.on("data", (chunk) => { output += String(chunk); });
  const client = new MastraMcpPythonClient({
    url: `http://127.0.0.1:${port}/mcp`,
    timeoutMs: 2_000,
  });

  try {
    await waitForPort(port);
    const result = await client.call("theme_chokepoint_get_run", {
      run_id: "python-1",
    });
    expect(result).toEqual({
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
    });
    expect(output).not.toMatch(/token=|provider|node_modules|Traceback/i);
  } finally {
    await client.close();
    child.kill();
    await new Promise<void>((resolveExit) => {
      if (child.exitCode !== null) resolveExit();
      else child.once("exit", () => resolveExit());
    });
  }
}, 90_000);
