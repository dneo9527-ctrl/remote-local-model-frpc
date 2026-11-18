$ErrorActionPreference = 'Stop'

function New-LocalSecret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

function Write-NewLocalFile([string]$Path, [string]$Content) {
    $bytes = ([Text.UTF8Encoding]::new($false)).GetBytes($Content)
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
}

foreach ($directory in @('frp', 'logs')) {
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
Write-Host 'Next: edit frp/frpc.toml (serverAddr, serverPort, auth.token), then run Start-Sub2API.cmd. Docker supplies frpc.'
