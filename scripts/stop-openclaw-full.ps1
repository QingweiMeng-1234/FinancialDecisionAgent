$ErrorActionPreference = "Stop"

# ──────────────────────────────────────────────────────
# Stop the full Financial Agent + OpenClaw stack
# 1. OpenClaw Docker container
# 2. financial-agent MCP server on host
# ──────────────────────────────────────────────────────

$StopMcpScript = Join-Path $PSScriptRoot "stop-financial-agent-mcp.ps1"
$OpenClawDockerDir = "C:\Users\weber\Tools\OpenClaw\docker"
$StopOpenClawScript = Join-Path $OpenClawDockerDir "stop-openclaw.ps1"

Write-Output "=========================================="
Write-Output " Financial Agent + OpenClaw - Full Stop"
Write-Output "=========================================="
Write-Output ""

# ── Step 1: Stop OpenClaw Docker ────────────────────

Write-Output "[1/2] Stopping OpenClaw Docker container..."
if (Test-Path $StopOpenClawScript) {
    Push-Location $OpenClawDockerDir
    try {
        & $StopOpenClawScript
    } finally {
        Pop-Location
    }
} else {
    Write-Output "  OpenClaw stop script not found, skipping."
}
Write-Output ""

# ── Step 2: Stop financial-agent MCP server ─────────

Write-Output "[2/2] Stopping financial-agent MCP server..."
& $StopMcpScript

Write-Output ""
Write-Output "=========================================="
Write-Output " Full stack stopped"
Write-Output "=========================================="
