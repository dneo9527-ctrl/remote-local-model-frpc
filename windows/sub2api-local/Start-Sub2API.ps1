param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Tunnel.ps1')
function Test-DockerReady {
    try {
        docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
try {
    Assert-Sub2ApiConfiguration
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI is not available.' }
    if (-not (Test-DockerReady)) {
        $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        if (-not (Test-Path -LiteralPath $desktop)) { throw 'Docker Desktop is not installed.' }
        Start-Process -FilePath $desktop -WindowStyle Hidden
        $ready = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Seconds 2
            if (Test-DockerReady) { $ready = $true; break }
        }
        if (-not $ready) { throw 'Docker Desktop is not ready. Open it and retry after its engine starts.' }
    }
    docker compose --project-directory $PSScriptRoot config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose configuration is invalid.' }
    docker compose --project-directory $PSScriptRoot up -d
    if ($LASTEXITCODE -ne 0) { throw 'Sub2API containers could not be started.' }
    $healthy = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/health' -TimeoutSec 3
            if ($health.status -eq 'ok') { $healthy = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) { throw 'Sub2API health check did not pass. Inspect Docker Desktop logs.' }
    Start-Sub2ApiTunnel
    Write-Host 'Local Sub2API: http://127.0.0.1:8080'
    Write-Host 'Mac visitor entry: http://127.0.0.1:18081 ; Codex API base URL: http://127.0.0.1:18081/v1'
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8080' }
} catch {
    Write-Error $_
    exit 1
}
