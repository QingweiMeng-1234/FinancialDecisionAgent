$ErrorActionPreference = "Stop"

$StartFullScript = Join-Path $PSScriptRoot "start-openclaw-full.ps1"

if (-not (Test-Path $StartFullScript)) {
    throw "Full stack start script not found: $StartFullScript"
}

Write-Output "============================================"
Write-Output " Financial Agent + OpenClaw - Full Restart"
Write-Output "============================================"
Write-Output ""

& $StartFullScript

Write-Output ""
Write-Output "============================================"
Write-Output " Full stack restart completed"
Write-Output "============================================"
