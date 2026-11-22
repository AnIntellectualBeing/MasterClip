param([int]$Port = 8766)
$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot "data/server-$Port.pid"
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output 'No studio server PID was recorded. Close its terminal window if it is running.'
    exit 0
}
$studioProcessId = 0
if (-not [int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$studioProcessId)) {
    throw 'Invalid studio PID file.'
}
$studioProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $studioProcessId"
$expectedScript = Join-Path $PSScriptRoot 'run.py'
if (-not $studioProcess) {
    Write-Output 'The studio server is already stopped.'
    exit 0
}
$normalizedCommand = $studioProcess.CommandLine.Replace('/', '\')
if (-not $normalizedCommand.Contains($expectedScript)) {
    throw 'The recorded process does not match this studio. No process was stopped.'
}
Stop-Process -Id $studioProcessId
Write-Output 'GTA Clip Studio stopped. Unfinished jobs will be marked Interrupted next time.'
