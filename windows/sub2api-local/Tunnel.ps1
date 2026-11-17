$ErrorActionPreference = 'Stop'
$script:DeployRoot = $PSScriptRoot
$script:FrpcPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'bin/frpc.exe'))
$script:FrpcConfig = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'frp/frpc.toml'))
$script:FrpcPidFile = Join-Path $PSScriptRoot 'frp/provider.pid'

function Assert-Sub2ApiConfiguration {
    $envPath = Join-Path $script:DeployRoot '.env'
    foreach ($required in @($envPath, $script:FrpcConfig, $script:FrpcPath)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw 'Missing local setup. Run Init-Sub2API.cmd, configure frp/frpc.toml and run Install-Frpc.cmd first.'
        }
    }
    if ([IO.File]::ReadAllText($envPath).Contains('CHANGE_ME_') -or
        [IO.File]::ReadAllText($script:FrpcConfig) -match 'CHANGE_ME_|frps\.example\.com') {
        throw 'Replace the remaining placeholders in .env and frp/frpc.toml before starting.'
    }
    New-Item -ItemType Directory -Path (Join-Path $script:DeployRoot 'logs') -Force | Out-Null
}

function Get-Sub2ApiTunnel {
    @(Get-CimInstance Win32_Process -Filter "Name='frpc.exe'" -ErrorAction Stop | Where-Object {
        $_.ExecutablePath -and
        [string]::Equals($_.ExecutablePath, $script:FrpcPath, [StringComparison]::OrdinalIgnoreCase) -and
        $_.CommandLine -and
        $_.CommandLine.IndexOf($script:FrpcConfig, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

function Start-Sub2ApiTunnel {
    & $script:FrpcPath verify -c $script:FrpcConfig
    if ($LASTEXITCODE -ne 0) { throw 'FRP configuration verification failed.' }
    $existing = @(Get-Sub2ApiTunnel)
    if ($existing.Count -gt 1) { throw 'More than one project tunnel found. Inspect before starting another.' }
    if ($existing.Count -eq 1) {
        Write-Host ('Reusing Sub2API tunnel PID ' + $existing[0].ProcessId)
        return
    }
    $process = Start-Process -FilePath $script:FrpcPath -ArgumentList @('-c', ('"' + $script:FrpcConfig + '"')) -WorkingDirectory $script:DeployRoot -WindowStyle Hidden -PassThru
    $process.Id | Set-Content -LiteralPath $script:FrpcPidFile -Encoding ascii
    Write-Host ('Sub2API tunnel started, PID ' + $process.Id + '. Check status for server registration.')
}

function Stop-Sub2ApiTunnel {
    foreach ($item in @(Get-Sub2ApiTunnel)) {
        $process = Get-Process -Id $item.ProcessId -ErrorAction Stop
        if (-not [string]::Equals($process.Path, $script:FrpcPath, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Process ownership changed; tunnel was not stopped.'
        }
        Stop-Process -Id $item.ProcessId -ErrorAction Stop
        Write-Host ('Stopped Sub2API tunnel PID ' + $item.ProcessId)
    }
}
