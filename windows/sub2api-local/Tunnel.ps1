$ErrorActionPreference = 'Stop'
$script:DeployRoot = $PSScriptRoot
$script:FrpcPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'bin/frpc.exe'))
$script:FrpcConfig = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'frp/frpc.toml'))

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
    $project = @([IO.File]::ReadAllLines($envPath) | Where-Object { $_ -match '^COMPOSE_PROJECT_NAME=' })
    if ($project.Count -ne 1) { throw 'Set one explicit COMPOSE_PROJECT_NAME in .env.' }
    $projectMatch = [Regex]::Match($project[0], '^COMPOSE_PROJECT_NAME=([a-z0-9][a-z0-9_-]{0,62})$')
    if (-not $projectMatch.Success) {
        throw 'Set one explicit lowercase COMPOSE_PROJECT_NAME in .env. Existing data requires the original name.'
    }
    $name = $projectMatch.Groups[1].Value
    # Explicit files/project prevent unrelated shell COMPOSE_FILE/PROJECT_NAME overrides.
    return @('compose', '--project-directory', $script:DeployRoot, '--env-file', $envPath,
             '--file', (Join-Path $script:DeployRoot 'compose.yaml'), '--project-name', $name)
}

function Assert-Sub2ApiCompose {
    $argsForCompose = @(Get-Sub2ApiComposeArguments)
    # Effective config contains secrets; capture it and never print it.
    $raw = & docker @argsForCompose config --format json 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration is invalid. Inspect local files privately.' }
    try { $effective = ($raw -join "`n") | ConvertFrom-Json }
    catch { throw 'Compose returned invalid configuration.' }
    $ports = @($effective.services.sub2api.ports)
    if ($ports.Count -ne 1 -or $ports[0].host_ip -ne '127.0.0.1' -or
        [string]$ports[0].published -ne '8080' -or [int]$ports[0].target -ne 8080 -or
        $ports[0].protocol -ne 'tcp' -or $null -ne $effective.services.postgres.ports -or
        $null -ne $effective.services.redis.ports) {
        throw 'Only Sub2API 127.0.0.1:8080 may be published. Remove shell overrides and public/database ports.'
    }
}

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
    $frpText = [IO.File]::ReadAllText($script:FrpcConfig)
    # Conservative acceptance of the supplied single-provider template. frpc
    # performs full TOML validation separately; includes cannot bypass policy.
    if ([Regex]::Matches($frpText, '(?m)^\s*\[\[proxies\]\]\s*(?:#.*)?$').Count -ne 1 -or
        $frpText -match '(?m)^\s*(?:includes\s*=|remotePort\s*=|\[\[visitors\]\])' -or
        $frpText -notmatch '(?m)^\s*type\s*=\s*"stcp"\s*(?:#.*)?$' -or
        $frpText -notmatch '(?m)^\s*localIP\s*=\s*"127\.0\.0\.1"\s*(?:#.*)?$' -or
        $frpText -notmatch '(?m)^\s*localPort\s*=\s*8080\s*(?:#.*)?$' -or
        $frpText -notmatch '(?m)^\s*transport\.tls\.enable\s*=\s*true\s*(?:#.*)?$' -or
        $frpText -notmatch '(?m)^\s*transport\.useEncryption\s*=\s*true\s*(?:#.*)?$') {
        throw 'Keep the supplied single encrypted STCP provider for 127.0.0.1:8080. Extra proxies/includes are not accepted.'
    }
    New-Item -ItemType Directory -Path (Join-Path $script:DeployRoot 'logs') -Force | Out-Null
}

function Get-Sub2ApiTunnel {
    # Our launcher uses -c "absolute path". Substrings can claim another deployment.
    $config = [Regex]::Escape($script:FrpcConfig)
    $configArgument = '(?i)(?:^|\s)-c\s+(?:"' + $config + '"|' + $config + ')(?=\s|$)'
    @(Get-CimInstance Win32_Process -Filter "Name='frpc.exe'" -ErrorAction Stop | Where-Object {
        $_.ExecutablePath -and
        [string]::Equals($_.ExecutablePath, $script:FrpcPath, [StringComparison]::OrdinalIgnoreCase) -and
        $_.CommandLine -and $_.CommandLine -match $configArgument
    })
}

function Start-Sub2ApiTunnel {
    try { $verification = & $script:FrpcPath verify -c $script:FrpcConfig 2>&1 }
    catch { throw 'FRP configuration verification failed. Inspect the private config locally.' }
    if ($LASTEXITCODE -ne 0) { throw 'FRP configuration verification failed. Inspect the private config locally.' }
    $existing = @(Get-Sub2ApiTunnel)
    if ($existing.Count -gt 1) { throw 'More than one project tunnel found. Inspect before starting another.' }
    if ($existing.Count -eq 1) {
        Write-Host ('Reusing Sub2API tunnel PID ' + $existing[0].ProcessId)
        return
    }
    $process = Start-Process -FilePath $script:FrpcPath -ArgumentList @('-c', ('"' + $script:FrpcConfig + '"')) -WorkingDirectory $script:DeployRoot -WindowStyle Hidden -PassThru
    Write-Host ('Sub2API tunnel started, PID ' + $process.Id + '. Check status for server registration.')
}

function Stop-Sub2ApiTunnel {
    foreach ($item in @(Get-Sub2ApiTunnel)) {
        $process = $null
        try {
            $process = Get-Process -Id $item.ProcessId -ErrorAction Stop
            # Keep an open OS handle while checking identity and terminating.
            # PID reuse cannot redirect Kill() to a newly created process.
            $null = $process.Handle
            $expectedTime = $item.CreationDate.ToUniversalTime().ToString('yyyyMMddHHmmss.ffffff')
            $actualTime = $process.StartTime.ToUniversalTime().ToString('yyyyMMddHHmmss.ffffff')
            if (-not [string]::Equals($process.Path, $script:FrpcPath, [StringComparison]::OrdinalIgnoreCase) -or $expectedTime -ne $actualTime) {
                throw 'Process ownership changed; tunnel was not stopped.'
            }
            $now = @(Get-Sub2ApiTunnel | Where-Object { $_.ProcessId -eq $item.ProcessId -and $_.CreationDate -eq $item.CreationDate })
            if ($now.Count -ne 1) { throw 'Process ownership changed; tunnel was not stopped.' }
            $process.Kill()
            if (-not $process.WaitForExit(10000)) { throw 'Project tunnel has not stopped yet. No other process was touched.' }
            Write-Host ('Stopped Sub2API tunnel PID ' + $item.ProcessId)
        } finally {
            if ($null -ne $process) { $process.Dispose() }
        }
    }
}
