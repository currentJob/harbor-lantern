param([int]$Port = 8091)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runtimeDir = Join-Path $projectRoot '.local'
$executable = Join-Path $runtimeDir 'cloudflared.exe'
if (-not (Test-Path -LiteralPath $executable)) { throw 'Install official cloudflared in .local first.' }
Invoke-RestMethod "http://127.0.0.1:$Port/api/health" | Out-Null
$tunnel = Start-Process -FilePath $executable -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -ArgumentList @('tunnel', '--no-autoupdate', '--protocol', 'http2', '--url', "http://127.0.0.1:$Port") `
    -RedirectStandardOutput (Join-Path $runtimeDir 'tunnel.stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDir 'tunnel.stderr.log')
$tunnel.Id | Set-Content (Join-Path $runtimeDir 'tunnel.pid')
Write-Output "Tunnel PID: $($tunnel.Id). HTTPS address appears in .local/tunnel.stderr.log."
