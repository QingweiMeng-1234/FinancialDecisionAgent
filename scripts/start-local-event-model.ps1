param(
    [string]$RuntimeRoot = (Join-Path $env:USERPROFILE 'Tools/financial-agent-local'),
    [int]$Port = 18081
)
$ErrorActionPreference = 'Stop'
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port must be between 1024 and 65535.' }
$serverPath = Join-Path $RuntimeRoot 'llama-b10809/llama-server.exe'
$modelPath = Join-Path $RuntimeRoot 'qwen2.5-1.5b-instruct-q4_k_m.gguf'
if (!(Test-Path -LiteralPath $serverPath) -or !(Test-Path -LiteralPath $modelPath)) {
    throw 'Missing llama.cpp server or model. See docs/local-event-structuring.md for official download sources.'
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Reuse the existing service or choose another port."
}
$modelArguments = @('-m', ('"' + $modelPath + '"'), '--host', '127.0.0.1', '--port', "$Port",
    '-c', '8192', '-t', '6', '--parallel', '1', '--alias', 'financial-event-qwen-1.5b-q4')
$modelProcess = Start-Process -FilePath $serverPath -ArgumentList $modelArguments -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $RuntimeRoot 'server.stdout.log') `
    -RedirectStandardError (Join-Path $RuntimeRoot 'server.stderr.log') -PassThru
$modelProcess.Id | Set-Content -LiteralPath (Join-Path $RuntimeRoot 'server.pid')
Write-Output "Started PID $($modelProcess.Id). Check http://127.0.0.1:$Port/health before inference."
