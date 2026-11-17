$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Tunnel.ps1')
Invoke-Sub2ApiLock {
    $argsForCompose = @(Get-Sub2ApiComposeArguments)
    Stop-Sub2ApiTunnel
    docker @argsForCompose stop
    if ($LASTEXITCODE -ne 0) { throw 'Container stop failed. Check Docker Desktop.' }
    Write-Host 'Sub2API stopped. Its database and named volumes are retained.'
}
