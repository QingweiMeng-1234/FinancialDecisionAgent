# Theme Chokepoint Mastra

Mastra is the operator and Studio projection layer. Python
`RootStageOrchestrator` remains the sole Stage 1–7 state-machine authority.

## Start locally

From the repository root, start the Python MCP authority:

```powershell
$env:PYTHONPATH = "src"
python -m event_collector.cli.financial_agent_mcp --transport streamable-http --port 8877
```

In a second terminal:

```powershell
cd apps/theme-chokepoint-mastra
npm ci
npm run dev
```

Open the Studio URL printed by `mastra dev`. Studio exposes:

- `Theme Chokepoint Operator`, a constrained Agent with lifecycle and projection
  tools;
- `theme-chokepoint-m0`, with visible Stage 1, confirmation, and Stage 2–7
  wrapper steps;
- `theme-chokepoint-historical-projection`, a seven-step read-only view for
  imported Python runs.

Use `THEME_CHOKEPOINT_MCP_URL` to select another loopback MCP port and
`MASTRA_STUDIO_DATA_DIR` to select the independent Studio data directory.

Historical projections can be created through the Agent's
`importHistoricalThemeRuns` tool. They retain the Python run ID, original
timestamps, contract lineage, and `imported: true`; importing never calls a
provider or mutates the Python run. Studio also performs the same idempotent
scan at startup by default; set `THEME_CHOKEPOINT_IMPORT_EXISTING_RUNS=false`
to disable that scan.
