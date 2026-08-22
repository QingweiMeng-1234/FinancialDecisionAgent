# Theme Chokepoint Mastra M0

This package is the persistent Mastra coordination layer for the existing
Python Theme Chokepoint authority. It uses a three-step Mastra 1.x workflow:

1. `start-python-stage1`
2. `await-product-confirmation` (the only suspendable step)
3. `continue-python-run`

It never implements Stage 1–7 transitions or writes the Python SQLite store.

## Configuration

- `THEME_CHOKEPOINT_MCP_URL` defaults to
  `http://127.0.0.1:8877/mcp`. Only an explicit localhost/127.0.0.1 HTTP
  port and the exact `/mcp` path are accepted.
- `MASTRA_STORAGE_URL` defaults to
  `file:./theme-chokepoint-mastra.db`.
- `LOG_LEVEL` is reserved for the hosting process. This package emits no
  prompt, environment, token, provider-body, or arbitrary-path logs.

Install and verify:

```sh
npm install --ignore-scripts --legacy-peer-deps
npm run typecheck
npm test
```

Programmatic entry points are exported from `src/index.ts`:
`createM0Service`, `startM0`, `resumeM0`, `getM0Status`, and
`getM0Artifacts` are available as service methods. Call `close()` when a
runtime instance is retired so its MCP and LibSQL handles are released.

## Proof limits

The tests use a deterministic injected MCP boundary and real temporary
file-backed LibSQL storage. They prove workflow suspension, restart/reopen,
confirmation ordering, concurrency failure closure, schema boundaries, and
root-manifest projection. They do not claim a live provider result, remote
deployment, web UI, external authentication, SSE-only interoperability, or
production filesystem cleanup. A real Python MCP process must be configured
with injected Theme providers before lifecycle tools can run.
