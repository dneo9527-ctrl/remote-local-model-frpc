$ErrorActionPreference = 'Stop'
$script:DeployRoot = $PSScriptRoot

function Invoke-Sub2ApiLock([scriptblock]$Action) {
    # One OS-owned lock per deployment; crashes release it automatically.
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($script:DeployRoot.ToLowerInvariant()))).Replace('-', '') }
    finally { $sha.Dispose() }
    $mutex = New-Object Threading.Mutex($false, ('Global\RemoteLocalModelFrpc-' + $hash))
    $held = $false
    try {
        try { $held = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $held = $true }
        if (-not $held) { throw 'This deployment is already starting, stopping or updating. Wait for it to finish.' }
        & $Action
    } finally {
        if ($held) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Get-Sub2ApiComposeArguments {
    $envPath = Join-Path $script:DeployRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw 'Missing .env. Run Init-Sub2API.cmd first.' }
    $projectLines = @([IO.File]::ReadAllLines($envPath) | Where-Object { $_ -match '^COMPOSE_PROJECT_NAME=' })
    if ($projectLines.Count -ne 1 -or $projectLines[0] -notmatch '^COMPOSE_PROJECT_NAME=([a-z0-9][a-z0-9_-]{0,62})$') {
        throw 'Keep one explicit COMPOSE_PROJECT_NAME in .env. Existing volumes require the original project name.'
    }
    $projectName = $projectLines[0].Substring('COMPOSE_PROJECT_NAME='.Length)
    return @('compose', '--project-directory', $script:DeployRoot, '--env-file', $envPath,
             '--file', (Join-Path $script:DeployRoot 'compose.yaml'), '--project-name', $projectName)
}

function Assert-Sub2ApiConfiguration {
    $configPath = Join-Path $script:DeployRoot 'frp/frpc.toml'
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'Missing private frp/frpc.toml. Run initialization first.' }
    $config = [IO.File]::ReadAllText($configPath)
    if ($config -match 'CHANGE_ME_|frps\.example\.com' -or
        $config -notmatch '(?m)^\s*localIP\s*=\s*"sub2api"\s*(?:#.*)?$' -or
        $config -notmatch '(?m)^\s*log\.to\s*=\s*"console"\s*(?:#.*)?$' -or
        $config -notmatch '(?m)^\s*transport\.tls\.enable\s*=\s*true\s*(?:#.*)?$' -or
        $config -notmatch '(?m)^\s*transport\.useEncryption\s*=\s*true\s*(?:#.*)?$') {
        throw 'Complete the encrypted Docker frpc config: localIP="sub2api", log.to="console", and valid server credentials.'
    }
    if ([Regex]::Matches($config, '(?m)^\s*\[\[proxies\]\]\s*(?:#.*)?$').Count -ne 1 -or
        $config -match '(?m)^\s*(?:includes\s*=|remotePort\s*=|\[\[visitors\]\])' -or
        $config -notmatch '(?m)^\s*type\s*=\s*"stcp"\s*(?:#.*)?$' -or
        $config -notmatch '(?m)^\s*localPort\s*=\s*8080\s*(?:#.*)?$') {
        throw 'Keep the supplied single encrypted STCP provider for sub2api:8080. Extra proxies/includes are not accepted.'
    }
    $composeArgs = @(Get-Sub2ApiComposeArguments)
    $raw = & docker @composeArgs config --format json 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration is invalid.' }
    try { $effective = ($raw -join "`n") | ConvertFrom-Json } catch { throw 'Compose returned invalid configuration.' }
    $ports = @($effective.services.sub2api.ports)
    if ($ports.Count -ne 1 -or $ports[0].host_ip -ne '127.0.0.1' -or
        [string]$ports[0].published -ne '8080' -or $ports[0].target -ne 8080 -or $ports[0].protocol -ne 'tcp' -or
        $effective.services.postgres.ports -or $effective.services.redis.ports -or
        $effective.services.'sub2api-frpc'.ports -or -not $effective.services.'sub2api-frpc') {
        throw 'Only Sub2API 127.0.0.1:8080 may be published; the Docker frpc service is required.'
    }
}

function Get-Sub2ApiTunnel {
    $composeArgs = @(Get-Sub2ApiComposeArguments)
    $containerId = & docker @composeArgs ps --all --quiet sub2api-frpc
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the project frpc container.' }
    if ($containerId) {
        $state = & docker inspect --format '{{json .State}}' $containerId
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the frpc container state.' }
        return ($state | ConvertFrom-Json)
    }
}

function Wait-Sub2ApiTunnel {
    $composeArgs = @(Get-Sub2ApiComposeArguments)
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $state = Get-Sub2ApiTunnel
        if ($state -and $state.Running) {
            # Only the current container start counts; old logins are excluded.
            $lines = @(& docker @composeArgs logs --no-color --since $state.StartedAt sub2api-frpc 2>&1)
            if ($LASTEXITCODE -eq 0 -and
                @($lines | Where-Object { $_ -match 'login to server success' }).Count -gt 0 -and
                @($lines | Where-Object { $_ -match '\[sub2api_ke\] start proxy success' }).Count -gt 0) {
                Write-Host 'Docker sub2api-frpc: logged in and registered sub2api_ke.'
                return
            }
        }
        Start-Sleep -Seconds 1
    }
    throw 'The frpc container has not registered sub2api_ke. Check sub2api-frpc logs in Docker Desktop.'
}

function Start-Sub2ApiTunnel {
    Assert-Sub2ApiConfiguration
    $composeArgs = @(Get-Sub2ApiComposeArguments)
    & docker @composeArgs up -d sub2api-frpc
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the Docker frpc service.' }
    Wait-Sub2ApiTunnel
}

function Stop-Sub2ApiTunnel {
    $composeArgs = @(Get-Sub2ApiComposeArguments)
    & docker @composeArgs stop sub2api-frpc
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the Docker frpc service.' }
}
