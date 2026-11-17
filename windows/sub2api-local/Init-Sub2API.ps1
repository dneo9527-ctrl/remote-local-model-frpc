$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Tunnel.ps1')

function New-LocalSecret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

function Write-NewLocalFile([string]$Path, [string]$Content) {
    $bytes = ([Text.UTF8Encoding]::new($false)).GetBytes($Content)
    $temporary = $Path + '.new-' + [Guid]::NewGuid().ToString('N')
    $stream = $null
    try {
        $stream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        # Restrict the new empty file before writing any secret bytes.
        $acl = New-Object Security.AccessControl.FileSecurity
        $acl.SetAccessRuleProtection($true, $false)
        $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl.SetOwner($user)
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($user, 'FullControl', 'Allow')))
        $system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($system, 'FullControl', 'Allow')))
        $stream.SetAccessControl($acl)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null
        # .NET Framework Move never overwrites an existing destination.
        [IO.File]::Move($temporary, $Path)
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}

Invoke-Sub2ApiLock {
foreach ($directory in @('bin', 'frp', 'logs')) {
    New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot $directory) -Force | Out-Null
}

$envPath = Join-Path $PSScriptRoot '.env'
if (Test-Path -LiteralPath $envPath) {
    Write-Host 'Keeping existing .env and its credentials.'
} else {
    $content = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '.env.example'))
    foreach ($name in @('POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'JWT_SECRET', 'TOTP_ENCRYPTION_KEY', 'ADMIN_PASSWORD')) {
        $content = $content.Replace(('CHANGE_ME_' + $name), (New-LocalSecret))
    }
    Write-NewLocalFile $envPath $content
    Write-Host 'Created .env with independent random credentials. Read ADMIN_EMAIL and ADMIN_PASSWORD locally to sign in.'
}

$frpPath = Join-Path $PSScriptRoot 'frp/frpc.toml'
if (Test-Path -LiteralPath $frpPath) {
    Write-Host 'Keeping existing frp/frpc.toml.'
} else {
    $content = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'frp/frpc.example.toml'))
    $content = $content.Replace('CHANGE_ME_STCP_SECRET', (New-LocalSecret))
    Write-NewLocalFile $frpPath $content
    Write-Host 'Created frp/frpc.toml with a new STCP secret.'
}
Write-Host 'Next: edit frp/frpc.toml (serverAddr, serverPort, auth.token), install frpc, then run Start-Sub2API.cmd.'
}
