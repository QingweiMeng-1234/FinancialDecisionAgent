import { mkdir, mkdtemp } from "node:fs/promises";
import { join } from "node:path";

import { expect, it } from "vitest";

import { createM0Runtime } from "../src/runtime.js";

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
