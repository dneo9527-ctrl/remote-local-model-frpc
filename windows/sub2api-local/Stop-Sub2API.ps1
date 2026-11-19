$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Tunnel.ps1')
Invoke-Sub2ApiLock {
$composeArgs = @(Get-Sub2ApiComposeArguments)
& docker @composeArgs stop
if ($LASTEXITCODE -ne 0) { throw 'Container stop failed. Check Docker Desktop.' }
Write-Host 'Sub2API stopped. Its database and other named volumes are retained.'

}
