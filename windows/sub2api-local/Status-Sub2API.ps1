$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Tunnel.ps1')
$composeArgs = @(Get-Sub2ApiComposeArguments)
& docker @composeArgs ps --all
if ($LASTEXITCODE -ne 0) { throw 'Docker project status is unavailable.' }
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health' -TimeoutSec 5
    Write-Host ('Local health: ' + $health.status)
} catch { Write-Host ('Local health unavailable: ' + $_.Exception.Message) }
& docker @composeArgs logs --no-color --tail 8 sub2api-frpc 2>&1 | ForEach-Object {
    $_.ToString() -replace '\b(?:\d{1,3}\.){3}\d{1,3}\b', '<host>' -replace '\[[a-f0-9]{16}\]', '[session]'
}
