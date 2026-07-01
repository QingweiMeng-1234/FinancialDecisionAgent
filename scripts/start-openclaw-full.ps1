$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StartMcpScript = Join-Path $PSScriptRoot "start-financial-agent-mcp.ps1"
$StopMcpScript = Join-Path $PSScriptRoot "stop-financial-agent-mcp.ps1"
$OpenClawDockerDir = "C:\Users\weber\Tools\OpenClaw\docker"
$StartOpenClawScript = Join-Path $OpenClawDockerDir "start-openclaw.ps1"
$StopOpenClawScript = Join-Path $OpenClawDockerDir "stop-openclaw.ps1"

Write-Output "=========================================="
Write-Output " Financial Agent + OpenClaw - Full Start"
Write-Output "=========================================="
Write-Output ""

Write-Output "[prep] Cleaning existing OpenClaw container if present..."
if (Test-Path $StopOpenClawScript) {
    Push-Location $OpenClawDockerDir
    try {
        & $StopOpenClawScript
    } catch {
        Write-Output "  OpenClaw cleanup skipped: $($_.Exception.Message)"
    } finally {
        Pop-Location
    }
}
Write-Output ""

Write-Output "[prep] Cleaning existing financial-agent MCP instance if present..."
& $StopMcpScript
Write-Output ""

Write-Output "[1/2] Starting financial-agent MCP server..."
& $StartMcpScript -ForceRestart
Write-Output ""

Write-Output "[2/2] Starting OpenClaw Docker container..."
Push-Location $OpenClawDockerDir
try {
    & $StartOpenClawScript
} finally {
    Pop-Location
}

Write-Output ""
Write-Output "=========================================="
Write-Output " Full stack started successfully"
Write-Output "  - financial-agent MCP: http://127.0.0.1:8877/mcp"
Write-Output "  - OpenClaw gateway:    http://127.0.0.1:18789"
Write-Output "=========================================="
