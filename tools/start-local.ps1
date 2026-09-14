param([int]$Port = 8091, [string]$PagesOrigin = 'https://currentjob.github.io')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runtimeDir = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Force $runtimeDir | Out-Null
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Check the existing backend before starting another."
}
$env:HL_HOST = '127.0.0.1'
$env:HL_PORT = [string]$Port
$env:HL_DB_PATH = Join-Path $runtimeDir 'harbor-lantern.db'
$env:HL_ALLOWED_ORIGINS = $PagesOrigin
$env:PYTHONUTF8 = '1'
$backend = Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\python.exe') `
    -ArgumentList 'run.py' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir 'backend.stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDir 'backend.stderr.log')
$backend.Id | Set-Content (Join-Path $runtimeDir 'backend.pid')
Write-Output "Backend PID: $($backend.Id); http://127.0.0.1:$Port"
