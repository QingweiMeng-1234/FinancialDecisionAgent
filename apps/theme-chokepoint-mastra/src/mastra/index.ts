import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

import { createM0Runtime } from "../runtime.js";

const dataDirectory = resolve(
  process.env.MASTRA_STUDIO_DATA_DIR ?? ".mastra-studio",
);
await mkdir(dataDirectory, { recursive: true });

const runtime = await createM0Runtime({
  databasePath: resolve(dataDirectory, "theme-chokepoint.sqlite"),
  mcpUrl:
    process.env.THEME_CHOKEPOINT_MCP_URL ?? "http://127.0.0.1:8877/mcp",
  mcpTimeoutMs: Number(
    process.env.THEME_CHOKEPOINT_MCP_TIMEOUT_MS ?? "5000",
  ),
  autoImportHistoricalRuns:
    process.env.THEME_CHOKEPOINT_IMPORT_EXISTING_RUNS !== "false",
});

export const mastra = runtime.mastra;
