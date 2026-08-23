import { spawn } from "node:child_process";
import { mkdir, mkdtemp } from "node:fs/promises";
import { createConnection, createServer } from "node:net";
import { join, resolve } from "node:path";

import { expect, it } from "vitest";

import { createM0Runtime } from "../src/runtime.js";

async function freePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>((ready) => server.listen(0, "127.0.0.1", ready));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>((closed) => server.close(() => closed()));
  return port;
}

async function waitForPort(port: number): Promise<void> {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const ready = await new Promise<boolean>((done) => {
      const socket = createConnection({ host: "127.0.0.1", port });
      socket.once("connect", () => { socket.destroy(); done(true); });
      socket.once("error", () => done(false));
    });
    if (ready) return;
    await new Promise((wait) => setTimeout(wait, 250));
  }
  throw new Error("Python MCP server did not become ready");
}

it("composes Mastra, LibSQL, the fixed workflow, service, and production MCP client", async () => {
  const tempRoot = join(process.cwd(), ".tmp");
  await mkdir(tempRoot, { recursive: true });
  const directory = await mkdtemp(join(tempRoot, "m0-runtime-"));
  const runtime = (await createM0Runtime({
    databasePath: join(directory, "state.sqlite"),
    mcpUrl: "http://127.0.0.1:1/mcp",
  })) as {
    mastra: { getWorkflow(name: string): { id: string } };
    service: unknown;
    close(): Promise<void>;
  };

  expect(runtime.mastra.getWorkflow("themeChokepointM0").id).toBe(
    "theme-chokepoint-m0",
  );
  expect(runtime.service).toBeDefined();
  await runtime.close();
});

it("drives the registered durable Mastra run through facade suspend and resume", async () => {
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
        PYTHONPATH: [
          resolve(repositoryRoot, "src"),
          resolve(repositoryRoot, "tests"),
        ].join(";"),
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  let runtime: Awaited<ReturnType<typeof createM0Runtime>> | undefined;
  try {
    await waitForPort(port);
    const tempRoot = join(process.cwd(), ".tmp");
    await mkdir(tempRoot, { recursive: true });
    const directory = await mkdtemp(join(tempRoot, "m0-production-workflow-"));
    runtime = await createM0Runtime({
      databasePath: join(directory, "state.sqlite"),
      mcpUrl: `http://127.0.0.1:${port}/mcp`,
    });
    const pythonRequest = {
      run_id: "python-production-1",
      theme: "AI data-center power infrastructure",
      trigger: "AI compute deployment growth",
      region: "global",
      as_of_date: "2026-08-16",
      time_horizon_months: 24,
      analysis_goal: "identify upstream chokepoints",
      seed_products: [],
      seed_companies: [],
      research_mode: "assisted",
      max_depth: 3,
      max_nodes: 30,
      max_iterations: 3,
      max_sources: 40,
      max_time_seconds: 900,
      max_cost_usd: 10,
      max_product_anchors: 2,
    };

    await expect(runtime.service.startM0({
      mastraRunId: "mastra-production-1",
      pythonRequest,
    })).resolves.toMatchObject({
      ok: true,
      data: { status: "AWAITING_PRODUCT_CONFIRMATION" },
    });
    const workflow = runtime.mastra.getWorkflow("themeChokepointM0");
    await expect(
      workflow.getWorkflowRunById("mastra-production-1"),
    ).resolves.toMatchObject({
      status: "suspended",
      payload: {
        mastraRunId: "mastra-production-1",
        pythonRunId: "python-production-1",
      },
    });

    await expect(runtime.service.resumeM0({
      mastraRunId: "mastra-production-1",
      actor: "owner",
      selectedAnchorIds: ["anchor-production-1"],
    })).resolves.toMatchObject({
      ok: true,
      data: {
        status: "SIGNAL_EXPORT_READY",
        confirmation: {
          runId: "python-production-1",
          confirmedAnchorIds: ["anchor-production-1"],
          confirmedBy: "owner",
        },
      },
    });
    await expect(
      workflow.getWorkflowRunById("mastra-production-1"),
    ).resolves.toMatchObject({ status: "success" });
  } finally {
    await runtime?.close();
    child.kill();
    await new Promise<void>((done) => {
      if (child.exitCode !== null) done();
      else child.once("exit", () => done());
    });
  }
}, 90_000);
