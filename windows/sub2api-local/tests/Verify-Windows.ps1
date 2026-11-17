# Native Windows PowerShell 5.1 regression suite. No containers or tunnels start.
$ErrorActionPreference = 'Stop'
$source = Split-Path $PSScriptRoot -Parent
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('frpc-windows-tests-' + [Guid]::NewGuid().ToString('N'))
$passed = 0
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:passed++
}
function Assert-Throws([scriptblock]$Action, [string]$Message) {
    $caught = $false
    try { & $Action | Out-Null } catch { $caught = $true }
    Assert-True $caught $Message
}
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    foreach ($name in @('Init-Sub2API.ps1', 'Install-Frpc.ps1', 'Start-Sub2API.ps1', 'Stop-Sub2API.ps1', 'Status-Sub2API.ps1', 'Tunnel.ps1', '.env.example', 'compose.yaml')) {
        Copy-Item -LiteralPath (Join-Path $source $name) -Destination $temporary
    }
    New-Item -ItemType Directory -Path (Join-Path $temporary 'frp') | Out-Null
    Copy-Item -LiteralPath (Join-Path $source 'frp/frpc.example.toml') -Destination (Join-Path $temporary 'frp')
    foreach ($file in Get-ChildItem -LiteralPath $source -Filter '*.ps1' -Recurse) {
        $tokens = $null; $errors = $null
        [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors) | Out-Null
        Assert-True ($errors.Count -eq 0) ('PowerShell parse failed: ' + $file.Name)
    }
    # Real isolated initialization, idempotency, and new-file ACL checks.
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $temporary 'Init-Sub2API.ps1') | Out-Null
    Assert-True ($LASTEXITCODE -eq 0) 'Initialization failed.'
    $envFile = Join-Path $temporary '.env'; $frpFile = Join-Path $temporary 'frp/frpc.toml'
    $before = @((Get-FileHash $envFile).Hash, (Get-FileHash $frpFile).Hash)
    $envContent = [IO.File]::ReadAllText($envFile)
    Assert-True (-not $envContent.Contains('CHANGE_ME_')) 'Initialization retained a credential placeholder.'
    $generated = @([Regex]::Matches($envContent, '(?m)^(?:POSTGRES_PASSWORD|REDIS_PASSWORD|JWT_SECRET|TOTP_ENCRYPTION_KEY|ADMIN_PASSWORD)=([a-f0-9]{64})') | ForEach-Object { $_.Groups[1].Value })
    Assert-True ($generated.Count -eq 5 -and @($generated | Select-Object -Unique).Count -eq 5) 'Generated credentials were missing or reused.'
    foreach ($file in @($envFile, $frpFile)) {
        $acl = Get-Acl -LiteralPath $file
        Assert-True $acl.AreAccessRulesProtected 'New private file retained inherited ACLs.'
        $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $allowed = @($currentSid, 'S-1-5-18')
        Assert-True ($acl.Access.Count -eq 2) 'Private file ACL is missing a required principal.'
        Assert-True ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq $currentSid) 'Private file owner is not the current user.'
        foreach ($rule in $acl.Access) {
            $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
            Assert-True ($sid -in $allowed) 'New private file grants access to another principal.'
            Assert-True (-not $rule.IsInherited -and $rule.AccessControlType -eq 'Allow' -and $rule.FileSystemRights -eq 'FullControl') 'Private file ACL is not an explicit FullControl allow rule.'
        }
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $temporary 'Init-Sub2API.ps1') | Out-Null
    Assert-True ($LASTEXITCODE -eq 0) 'Repeated initialization failed.'
    Assert-True ((Get-FileHash $envFile).Hash -eq $before[0] -and (Get-FileHash $frpFile).Hash -eq $before[1]) 'Repeated initialization overwrote private files.'
    . (Join-Path $temporary 'Tunnel.ps1')
    # Placeholder rejection must happen before any Docker or frpc execution.
    Assert-Throws { Assert-Sub2ApiConfiguration } 'Unconfigured deployment passed preflight.'
    # Only the expected encrypted STCP local application may be published.
    [IO.File]::WriteAllText($script:FrpcPath, 'fixture-only-not-executable')
    $frpGood = [IO.File]::ReadAllText($frpFile).Replace('frps.example.com', 'relay.invalid').Replace('CHANGE_ME_FRPS_TOKEN', 'fixture-only')
    [IO.File]::WriteAllText($frpFile, $frpGood)
    Assert-Sub2ApiConfiguration
    $script:passed++
    [IO.File]::WriteAllText($frpFile, $frpGood.Replace('localIP = "127.0.0.1"', 'localIP = "192.0.2.1"'))
    Assert-Throws { Assert-Sub2ApiConfiguration } 'Non-loopback STCP application was accepted.'
    [IO.File]::WriteAllText($frpFile, ('includes = ["other.toml"]' + "`n" + $frpGood))
    Assert-Throws { Assert-Sub2ApiConfiguration } 'Additional FRP includes were accepted.'
    [IO.File]::WriteAllText($frpFile, $frpGood)
    $composeArgs = @(Get-Sub2ApiComposeArguments)
    Assert-True ($composeArgs -contains '--file' -and $composeArgs -contains '--env-file' -and $composeArgs[-1] -eq 'sub2api-local') 'Compose identity not explicit.'
    # No actual Docker invocation: effective config comes from this test fixture.
    $script:FixtureHost = '127.0.0.1'
    function docker {
        $global:LASTEXITCODE = 0
        @{services = @{sub2api = @{ports = @(@{host_ip=$script:FixtureHost;published='8080';target=8080;protocol='tcp'})};postgres=@{};redis=@{}}} | ConvertTo-Json -Depth 8 -Compress
    }
    Assert-Sub2ApiCompose
    $script:passed++
    $script:FixtureHost = '0.0.0.0'
    Assert-Throws { Assert-Sub2ApiCompose } 'Effective public binding was accepted.'
    # Exact ownership: prefix paths and other executables are not ours.
    $script:FixtureProcesses = @(
        [pscustomobject]@{ExecutablePath=$script:FrpcPath;CommandLine=('"' + $script:FrpcPath + '" -c "' + $script:FrpcConfig + '"');ProcessId=101;CreationDate=[datetime]'2026-11-17T00:00:00Z'},
        [pscustomobject]@{ExecutablePath=$script:FrpcPath;CommandLine=('frpc.exe -c "' + $script:FrpcConfig + '.other"');ProcessId=102;CreationDate='other'},
        [pscustomobject]@{ExecutablePath='C:\unrelated\frpc.exe';CommandLine=('frpc.exe -c "' + $script:FrpcConfig + '"');ProcessId=103;CreationDate='other'}
    )
    function Get-CimInstance { param($Filter, $ErrorAction) $script:FixtureProcesses }
    $found = @(Get-Sub2ApiTunnel)
    Assert-True ($found.Count -eq 1 -and $found[0].ProcessId -eq 101) 'Ownership matched an unrelated process.'
    $script:Stopped = @()
    function Get-Process {
        param($Id, $ErrorAction)
        $p = [pscustomobject]@{Id=$Id;Handle=1;Path=$script:FrpcPath;StartTime=[datetime]'2026-11-17T00:00:00Z'}
        $p | Add-Member ScriptMethod Kill { $script:Stopped += $this.Id }
        $p | Add-Member ScriptMethod WaitForExit { param($Timeout) return $true }
        $p | Add-Member ScriptMethod Dispose { }
        return $p
    }
    Stop-Sub2ApiTunnel
    Assert-True ($script:Stopped.Count -eq 1 -and $script:Stopped[0] -eq 101) 'Stop touched an unrelated process.'
    # An independent Windows process must be denied while this mutex is held.
    $child = Join-Path $temporary 'Lock-Probe.ps1'
    [IO.File]::WriteAllText($child, ". (Join-Path `$PSScriptRoot 'Tunnel.ps1')`ntry { Invoke-Sub2ApiLock { }; exit 99 } catch { exit 23 }`n")
    Invoke-Sub2ApiLock {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $child
        Assert-True ($LASTEXITCODE -eq 23) 'Concurrent deployment operation was accepted.'
    }
    Invoke-Sub2ApiLock { $script:passed++ }
    Write-Host ('Windows regression checks passed: ' + $passed)
} finally {
    # Only this suite's unique temporary deployment is removed.
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
