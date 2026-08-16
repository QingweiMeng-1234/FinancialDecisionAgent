# OpenClaw Docker Isolation Runbook

This repo now supports a Docker-isolated `OpenClaw` runtime that talks to the host-local `financial-agent` MCP server over Streamable HTTP.

## Boundaries

- `financial-agent` stays in this repo and runs from this repo's `.venv`
- `OpenClaw` lives under `C:\Users\weber\Tools\OpenClaw`
- the only live OpenClaw MCP mapping is `C:\Users\weber\Tools\OpenClaw\.mcp.json`
- host/container connectivity is `http://host.docker.internal:8877/mcp`
- the host-side MCP service binds only to `127.0.0.1`

## Host-side financial-agent startup

Use `scripts/start-financial-agent-mcp.ps1` as the controlled launcher. It:

- uses only this repo's `.venv`
- checks the MCP entrypoint and shared defaults file
- checks that `127.0.0.1:8877` is free
- starts the MCP server in `streamable-http` mode
- writes deterministic stdout/stderr logs under `logs/financial-agent-mcp`

Stop it with `scripts/stop-financial-agent-mcp.ps1`.

## Shared defaults

`financial-agent` CLI flows and MCP flows now share one defaults file:

- `config/financial_agent_service_defaults.json`

The normal OpenClaw-facing parameter surface stays intentionally small:

- `top_k`
- `retrieval_top_k`

Advanced retrieval tuning remains a CLI workflow.

## OpenClaw runtime root

The isolated runtime lives outside the repo:

```text
C:\Users\weber\Tools\OpenClaw\
  .mcp.json
  config\
  logs\
  cache\
  docker\
  src\openclaw\
  README.md
  INSTALL_SOURCE.txt
```

The pinned upstream snapshot currently resolves to commit:

- `69974530987c3ba713072e7f0fe116fc1e00acbd`

## Docker entrypoints

Under `C:\Users\weber\Tools\OpenClaw\docker\`:

- `docker-compose.yml`
- `.env`
- `.env.example`
- `start-openclaw.ps1`
- `stop-openclaw.ps1`

`start-openclaw.ps1` is the preferred entrypoint because it validates the pinned source checkout, `.mcp.json`, and runtime config before running `docker compose up --build -d`.

## Gateway auth

The OpenClaw Docker gateway refuses non-loopback-style container binds without shared-secret auth. This setup uses a local gateway token stored in:

- `C:\Users\weber\Tools\OpenClaw\docker\.env`

The startup script now fails fast if `OPENCLAW_GATEWAY_TOKEN` is missing, instead of reporting a false-positive startup while the container crash-loops.

For the current local setup, open the dashboard with:

- `http://127.0.0.1:18789/#token=weber-openclaw-local`

After the first successful load, the browser keeps the token in session storage for that tab.
