param(
    [switch]$ForceRestart
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$EntryPoint = Join-Path $RepoRoot "financial_agent_mcp.py"
$DefaultsPath = Join-Path $RepoRoot "config\financial_agent_service_defaults.json"
$EnvFile = Join-Path $RepoRoot ".env"
$LogDir = Join-Path $RepoRoot "logs\financial-agent-mcp"
$StdOutLog = Join-Path $LogDir "stdout.log"
$StdErrLog = Join-Path $LogDir "stderr.log"
$PidFile = Join-Path $LogDir "financial-agent-mcp.pid"
$HostBind = "127.0.0.1"
$Port = 8877

function Assert-PathExists([string]$PathValue, [string]$Label) {
    if (-not (Test-Path $PathValue)) {
        throw "$Label not found: $PathValue"
    }
}

function Quote-Arg([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Import-DotEnv([string]$PathValue) {
    if (-not (Test-Path $PathValue)) {
        return
    }

    foreach ($line in Get-Content -Path $PathValue) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }

        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }

        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
            $quote = $value.Substring(0, 1)
            if (($quote -eq "'" -or $quote -eq '"') -and $value.EndsWith($quote)) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        if (-not [string]::IsNullOrWhiteSpace($key)) {
            Set-Item -Path ("Env:" + $key) -Value $value
        }
    }
}

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

function Test-FinancialAgentProcess([int]$ProcessId, [string]$ExpectedEntryPoint) {
    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return $false
    }
    return $commandLine -like "*financial_agent_mcp.py*"
}

function Stop-ProcessIfRunning([int]$ProcessId, [string]$Reason) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }
    Write-Output "Stopping existing financial-agent MCP process $ProcessId ($Reason)..."
    Stop-Process -Id $ProcessId -Force
    Start-Sleep -Seconds 1
    return $true
}

Assert-PathExists $PythonExe "Repo virtualenv Python"
Assert-PathExists $EntryPoint "financial-agent MCP entrypoint"
Assert-PathExists $DefaultsPath "Shared service defaults config"
Import-DotEnv $EnvFile

$portOwnerPid = Get-PortOwnerProcessId -BindHost $HostBind -BindPort $Port
if ($null -ne $portOwnerPid) {
    $knownFinancialAgent = Test-FinancialAgentProcess -ProcessId $portOwnerPid -ExpectedEntryPoint $EntryPoint

    if ($ForceRestart -and $knownFinancialAgent) {
        Stop-ProcessIfRunning -ProcessId $portOwnerPid -Reason "force restart requested" | Out-Null
        if (Test-Path $PidFile) {
            Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
        }
        $portOwnerPid = Get-PortOwnerProcessId -BindHost $HostBind -BindPort $Port
    }

    if ($null -ne $portOwnerPid) {
        if ($knownFinancialAgent -and -not $ForceRestart) {
            Write-Output "financial-agent MCP is already running on http://$HostBind`:$Port/mcp (PID $portOwnerPid)."
            Set-Content -Path $PidFile -Value $portOwnerPid -Encoding ascii
            exit 0
        }

        $ownerProcess = Get-Process -Id $portOwnerPid -ErrorAction SilentlyContinue
        $ownerPath = if ($null -ne $ownerProcess) { $ownerProcess.Path } else { "unknown" }
        throw "Port $Port is already in use on $HostBind by PID $portOwnerPid ($ownerPath). Use -ForceRestart only for an existing financial-agent MCP process, or free the port first."
    }
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$arguments = @(
    (Quote-Arg $EntryPoint),
    "--transport",
    "streamable-http",
    "--host",
    $HostBind,
    "--port",
    "$Port",
    "--service-defaults-path",
    (Quote-Arg $DefaultsPath)
) -join " "

$process = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $arguments `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -WindowStyle Hidden `
    -PassThru

Set-Content -Path $PidFile -Value $process.Id -Encoding ascii
Write-Output "financial-agent MCP started on http://$HostBind`:$Port/mcp (PID $($process.Id))"
