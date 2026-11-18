$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Tunnel.ps1')
$composeArgs = @(Get-Sub2ApiComposeArguments)
& docker @composeArgs ps --all
if ($LASTEXITCODE -ne 0) { throw 'Docker project status is unavailable.' }
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health' -TimeoutSec 5
    Write-Host ('Local health: ' + $health.status)
} catch { Write-Host ('Local health unavailable: ' + $_.Exception.Message) }
$state = Get-Sub2ApiTunnel
if ($state) { Write-Host ('Project frpc container running: ' + $state.Running) }
Write-Host 'For private registration details, inspect sub2api-frpc Logs in Docker Desktop.'
