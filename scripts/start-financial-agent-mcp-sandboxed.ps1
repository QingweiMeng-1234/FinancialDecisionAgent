param(
    [switch]$ForceRestart,
    [switch]$DebugSandbox
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoPythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$EntryPoint = Join-Path $RepoRoot "financial_agent_mcp.py"
$DefaultsPath = Join-Path $RepoRoot "config\financial_agent_service_defaults.json"
$SandboxSettings = Join-Path $RepoRoot "config\sandbox\financial-agent-mcp.srt.json"
$SrtCli = Join-Path $RepoRoot "node_modules\@anthropic-ai\sandbox-runtime\dist\cli.js"
$EnvFile = Join-Path $RepoRoot ".env"
$LogDir = Join-Path $RepoRoot "logs\financial-agent-mcp"
$StdOutLog = Join-Path $LogDir "stdout.log"
$StdErrLog = Join-Path $LogDir "stderr.log"
$PidFile = Join-Path $LogDir "financial-agent-mcp.pid"
$SandboxTempDir = Join-Path $RepoRoot "data\runtime\sandbox-tmp"
$HostBind = "127.0.0.1"
$Port = 8877

function Assert-PathExists([string]$PathValue, [string]$Label) {
    if (-not (Test-Path -LiteralPath $PathValue)) {
        throw "$Label not found: $PathValue"
    }
}

function Test-PythonRuntime([string]$Candidate) {
    if ([string]::IsNullOrWhiteSpace($Candidate) -or -not (Test-Path -LiteralPath $Candidate)) {
        return $false
    }
    try {
        & $Candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Resolve-PythonRuntime() {
    if (Test-PythonRuntime $RepoPythonExe) {
        return $RepoPythonExe
    }
    $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $systemPython -and (Test-PythonRuntime $systemPython.Source)) {
        return $systemPython.Source
    }
    throw "No working Python 3.10+ runtime found. The repo virtualenv may point to a Python installation from another machine."
}

function Quote-Arg([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Import-DotEnv([string]$PathValue) {
    if (-not (Test-Path -LiteralPath $PathValue)) {
        return
    }

    foreach ($line in Get-Content -LiteralPath $PathValue) {
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

function Test-FinancialAgentProcess([int]$ProcessId) {
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

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "This launcher currently targets the Sandbox Runtime Windows backend."
}

Assert-PathExists $EntryPoint "financial-agent MCP entrypoint"
Assert-PathExists $DefaultsPath "Shared service defaults config"
Assert-PathExists $SandboxSettings "Sandbox Runtime settings"
Assert-PathExists $SrtCli "Repository-local Sandbox Runtime CLI; run npm install"

$NodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if ($null -eq $NodeCommand) {
    throw "Node.js 20.11+ is required for Sandbox Runtime."
}
$NodeExe = $NodeCommand.Source
& $NodeExe -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 20 || (major === 20 && minor >= 11) ? 0 : 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Node.js 20.11+ is required for Sandbox Runtime."
}

$sandboxAccount = Get-CimInstance Win32_UserAccount -Filter "LocalAccount=True AND Name='srt-sandbox'" -ErrorAction SilentlyContinue
if ($null -eq $sandboxAccount) {
    throw "Sandbox Runtime is not provisioned. From an elevated-capable terminal, run: .\node_modules\.bin\srt.cmd windows-install"
}

$PythonExe = Resolve-PythonRuntime
$sitePackages = Join-Path $RepoRoot ".venv\Lib\site-packages"
$pythonPathEntries = @((Join-Path $RepoRoot "src"))
if (Test-Path -LiteralPath $sitePackages) {
    $pythonPathEntries += $sitePackages
}
$env:PYTHONPATH = $pythonPathEntries -join [IO.Path]::PathSeparator
& $PythonExe -c "import chromadb, mcp, event_collector" *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Selected Python runtime cannot import the financial-agent MCP dependencies."
}

$sandboxSettingsObject = Get-Content -LiteralPath $SandboxSettings -Raw | ConvertFrom-Json
if ($sandboxSettingsObject.windows.proxyPortRange[0] -ne 60080 -or $sandboxSettingsObject.windows.proxyPortRange[1] -ne 60089) {
    throw "Sandbox settings proxyPortRange must match the Windows installation range 60080-60089."
}

Import-DotEnv $EnvFile
New-Item -ItemType Directory -Force -Path $LogDir, $SandboxTempDir | Out-Null
$env:TEMP = $SandboxTempDir
$env:TMP = $SandboxTempDir

$portOwnerPid = Get-PortOwnerProcessId -BindHost $HostBind -BindPort $Port
if ($null -ne $portOwnerPid) {
    $knownFinancialAgent = Test-FinancialAgentProcess -ProcessId $portOwnerPid

    if ($ForceRestart -and $knownFinancialAgent) {
        Stop-ProcessIfRunning -ProcessId $portOwnerPid -Reason "force restart requested" | Out-Null
        if (Test-Path -LiteralPath $PidFile) {
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        }
        $portOwnerPid = Get-PortOwnerProcessId -BindHost $HostBind -BindPort $Port
    }

    if ($null -ne $portOwnerPid) {
        if ($knownFinancialAgent -and -not $ForceRestart) {
            Write-Output "financial-agent MCP is already running on http://$HostBind`:$Port/mcp (PID $portOwnerPid)."
            Set-Content -LiteralPath $PidFile -Value $portOwnerPid -Encoding ascii
            exit 0
        }

        $ownerProcess = Get-Process -Id $portOwnerPid -ErrorAction SilentlyContinue
        $ownerPath = if ($null -ne $ownerProcess) { $ownerProcess.Path } else { "unknown" }
        throw "Port $Port is already in use on $HostBind by PID $portOwnerPid ($ownerPath). Use -ForceRestart only for an existing financial-agent MCP process, or free the port first."
    }
}

$sandboxArgs = @(
    (Quote-Arg $SrtCli),
    "--settings",
    (Quote-Arg $SandboxSettings)
)
if ($DebugSandbox) {
    $sandboxArgs += "--debug"
}
$sandboxArgs += @(
    "--",
    (Quote-Arg $PythonExe),
    (Quote-Arg $EntryPoint),
    "--transport",
    "streamable-http",
    "--host",
    $HostBind,
    "--port",
    "$Port",
    "--service-defaults-path",
    (Quote-Arg $DefaultsPath)
)

$sandboxProcess = Start-Process `
    -FilePath $NodeExe `
    -ArgumentList ($sandboxArgs -join " ") `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog `
    -WindowStyle Hidden `
    -PassThru

$deadline = [DateTime]::UtcNow.AddSeconds(45)
do {
    $sandboxProcess.Refresh()
    if ($sandboxProcess.HasExited) {
        $errorTail = if (Test-Path -LiteralPath $StdErrLog) {
            (Get-Content -LiteralPath $StdErrLog -Tail 20) -join [Environment]::NewLine
        }
        else {
            "No stderr log was created."
        }
        throw "Sandboxed MCP failed during startup (exit $($sandboxProcess.ExitCode)). $errorTail"
    }

    $portOwnerPid = Get-PortOwnerProcessId -BindHost $HostBind -BindPort $Port
    if ($null -ne $portOwnerPid) {
        if (-not (Test-FinancialAgentProcess -ProcessId $portOwnerPid)) {
            Stop-Process -Id $sandboxProcess.Id -Force -ErrorAction SilentlyContinue
            throw "Port $Port became occupied by an unexpected process (PID $portOwnerPid)."
        }

        Set-Content -LiteralPath $PidFile -Value $portOwnerPid -Encoding ascii
        Write-Output "Sandboxed financial-agent MCP started on http://$HostBind`:$Port/mcp (PID $portOwnerPid; sandbox broker PID $($sandboxProcess.Id))."
        exit 0
    }

    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)

Stop-Process -Id $sandboxProcess.Id -Force -ErrorAction SilentlyContinue
throw "Sandboxed MCP did not bind http://$HostBind`:$Port within 45 seconds. Check $StdErrLog."
