param(
    [ValidateSet('check', 'stage', 'baseline', 'select', 'rollback', 'status')][string]$Action = 'check',
    [ValidateSet('stable', 'preview')][string]$Channel = 'preview',
    [string]$State,
    [string]$Version,
    [string]$Source,
    [string]$Python
)
$ErrorActionPreference = 'Stop'
# Optional maintenance helper. Existing Windows deployment does not require Python.
if (-not $Python) {
    if (Get-Command py -ErrorAction SilentlyContinue) { $Python = 'py'; $prefix = @('-3') }
    elseif (Get-Command python3 -ErrorAction SilentlyContinue) { $Python = 'python3'; $prefix = @() }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = 'python'; $prefix = @() }
    else { throw 'This optional source updater needs an existing Python 3.11+. No software was installed.' }
} else { $prefix = @() }
& $Python @prefix -c 'import sys; sys.exit(sys.version_info < (3, 11))'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required for the optional source updater.' }
$arguments = @((Join-Path $PSScriptRoot 'update.py'), $Action, '--channel', $Channel)
if ($State) { $arguments += @('--state', $State) }
if ($Version) { $arguments += @('--version', $Version) }
if ($Source) { $arguments += @('--source', $Source) }
& $Python @prefix @arguments
exit $LASTEXITCODE
