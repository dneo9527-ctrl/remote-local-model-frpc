$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Tunnel.ps1')
$argsForCompose = @(Get-Sub2ApiComposeArguments)
docker @argsForCompose ps
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health' -TimeoutSec 5
    Write-Host ('Local health: ' + $health.status)
} catch { Write-Host 'Local health unavailable. Check this deployment privately.' }
@(Get-Sub2ApiTunnel) | Select-Object ProcessId
Write-Host 'Read logs/frpc.log locally for registration details; logs are not printed by this command.'
