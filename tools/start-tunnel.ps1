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

# 주소가 로그에 찍힐 때까지 기다렸다가 접속 링크를 Telegram 으로 보낸다.
# 주소는 재시작마다 바뀌므로(그래서 배포 시점에 굽지 않는다) 사람이 따라다니지 않게 한다.
# 설정이 없으면 notify_tunnel.py 가 조용히 건너뛴다 — 알림 실패로 터널을 죽이지 않는다.
# 기기별 경로는 .env.local 에서 읽는다(비밀값은 복사하지 않고 경로만 가리킨다).
$envFile = Join-Path $projectRoot '.env.local'
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+?)\s*$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
        }
    }
}
$log = Join-Path $runtimeDir 'tunnel.stderr.log'
$deadline = (Get-Date).AddSeconds(45)
do {
    Start-Sleep -Milliseconds 700
    $found = (Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue)
} while (-not $found -and (Get-Date) -lt $deadline)
if (-not $found) { Write-Warning '터널 주소를 45초 안에 받지 못했다. .local/tunnel.stderr.log 를 확인하라.'; return }
& (Join-Path $projectRoot '.venv/Scripts/python.exe') (Join-Path $projectRoot 'tools/notify_tunnel.py')
