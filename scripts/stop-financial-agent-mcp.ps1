$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $RepoRoot "logs\financial-agent-mcp"
$PidFile = Join-Path $LogDir "financial-agent-mcp.pid"
$HostBind = "127.0.0.1"
$Port = 8877

function Get-PortOwnerProcessId([string]$BindHost, [int]$BindPort) {
    $connection = Get-NetTCPConnection -LocalAddress $BindHost -LocalPort $BindPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $connection) {
        return $null
    }
    return [int]$connection.OwningProcess
}

function Get-ProcessCommandLine([int]$ProcessId) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo) {
        return ""
    }
    return [string]$processInfo.CommandLine
}

function Test-FinancialAgentProcess([int]$ProcessId) {
    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return $false
    }
    return $commandLine -like "*financial_agent_mcp.py*"
}

function Stop-FinancialAgentProcess([int]$ProcessId, [string]$Reason) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }
    Stop-Process -Id $ProcessId -Force
    Write-Output "financial-agent MCP stopped (PID $ProcessId, $Reason)."
    return $true
}

$stopped = $false

if (Test-Path $PidFile) {
    $pidValue = [int](Get-Content $PidFile | Select-Object -First 1)
    $stopped = Stop-FinancialAgentProcess -ProcessId $pidValue -Reason "pid file"
    Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
}

if (-not $stopped) {
    $portOwnerPid = Get-PortOwnerProcessId -BindHost $HostBind -BindPort $Port
    if ($null -ne $portOwnerPid -and (Test-FinancialAgentProcess -ProcessId $portOwnerPid)) {
        $stopped = Stop-FinancialAgentProcess -ProcessId $portOwnerPid -Reason "port owner fallback"
    }
}

if (-not $stopped) {
    Write-Output "financial-agent MCP is not running; nothing to stop."
}
