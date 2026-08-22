import { Mastra } from "@mastra/core";
import { LibSQLStore } from "@mastra/libsql";
import { z } from "zod";

import { MastraMcpPythonClient } from "./mcp-client.js";
import { M0Service } from "./service.js";
import { LibSqlM0StateStore } from "./state.js";
import { createM0Workflow } from "./workflow.js";

const optionsSchema = z
  .object({
    databasePath: z.string().min(1),
    mcpUrl: z.string().url(),
  })
  .strict();

export async function createM0Runtime(options: unknown) {
  const parsed = optionsSchema.parse(options);
  const state = await LibSqlM0StateStore.open(parsed.databasePath);
  const mastraStorage = new LibSQLStore({
    id: "theme-chokepoint-m0-storage",
    url: `file:${parsed.databasePath}.mastra`,
  });
  await mastraStorage.init();
  const python = new MastraMcpPythonClient({ url: parsed.mcpUrl });
  const mastra = new Mastra({
    storage: mastraStorage,
    workflows: { themeChokepointM0: createM0Workflow() },
  });
  const service = new M0Service({
    store: state,
    workflow: state,
    python,
  });
  return {
    mastra,
    service,
    async close() {
      await python.close();
      await state.close();
      await mastraStorage.close();
    },
  };
}
