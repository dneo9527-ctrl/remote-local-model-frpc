param([string]$Config = $env:REMOTE_MODEL_CONFIG, [string]$State = $env:REMOTE_MODEL_UPDATE_STATE,
      [string]$Python, [switch]$Foreground)
$ErrorActionPreference = 'Stop'
if (-not $Config) { throw 'Supply -Config with a private settings file outside the source tree.' }
if (-not $Python) {
    if (Get-Command py -ErrorAction SilentlyContinue) { $Python = 'py'; $prefix = @('-3') }
    elseif (Get-Command python3 -ErrorAction SilentlyContinue) { $Python = 'python3'; $prefix = @() }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = 'python'; $prefix = @() }
    else { throw 'The optional web console needs existing Python 3.11+. Windows deployment scripts remain usable without it.' }
} else { $prefix = @() }
& $Python @prefix -c 'import sys; sys.exit(sys.version_info < (3, 11))'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required for the optional web console.' }
$arguments = @('-B', (Join-Path $PSScriptRoot 'launch.py'), '--config', $Config)
if ($State) { $arguments += @('--state', $State) }
if ($Foreground) { $arguments += '--foreground' }
& $Python @prefix @arguments
exit $LASTEXITCODE
