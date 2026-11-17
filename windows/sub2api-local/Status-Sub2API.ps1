$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Tunnel.ps1')
docker compose --project-directory $PSScriptRoot ps
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health' -TimeoutSec 5
    Write-Host ('Local health: ' + $health.status)
} catch { Write-Host ('Local health unavailable: ' + $_.Exception.Message) }
@(Get-Sub2ApiTunnel) | Select-Object ProcessId, ExecutablePath
$log = Join-Path $PSScriptRoot 'logs\frpc.log'
if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 12 }
