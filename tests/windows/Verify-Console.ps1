#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$source = Join-Path $repo 'windows\local-model-console'
$output = [IO.Path]::GetFullPath($OutputDirectory)
[void][IO.Directory]::CreateDirectory($output)
$results = [Collections.Generic.List[object]]::new()
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$utf8 = [Text.UTF8Encoding]::new($false)

function Invoke-IsolatedCheck {
    param([string]$Name, [string]$Script, [string[]]$Arguments=@(), [string]$CountPattern='', [int]$MinimumCount=1)
    # A fresh interpreter prevents dot-sourced fixture functions from one test
    # replacing production functions used by the next test.
    $previousErrorAction = $ErrorActionPreference
    try {
        # Capture native stderr in the artifact even when a child check fails.
        $ErrorActionPreference = 'Continue'
        $lines = @(& $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -STA -File $Script @Arguments 2>&1)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousErrorAction }
    $global:LASTEXITCODE = 0
    $text = ($lines | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
    [IO.File]::WriteAllText((Join-Path $output ($Name+'.txt')), $text, $utf8)
    $count = $null
    if ($CountPattern) {
        $match = [regex]::Match($text, $CountPattern)
        if ($match.Success) { $count = [int]$match.Groups[1].Value }
    }
    $results.Add([pscustomobject]@{Name=$Name; ExitCode=$code; Passed=$count})
    Write-Host $text
    if ($code -ne 0) { throw ($Name+' failed with exit '+$code+'. See its artifact log.') }
    if ($CountPattern -and ($null -eq $count -or $count -lt $MinimumCount)) { throw ($Name+' did not report its required passing check count.') }
}

$success = $false
try {
    # Preserve the source's PowerShell 5.1 UTF-8-with-BOM contract.
    foreach ($file in Get-ChildItem -LiteralPath $source -Filter '*.ps1' -File -Recurse) {
        $bytes = [IO.File]::ReadAllBytes($file.FullName)
        if ($bytes.Length -lt 3 -or $bytes[0] -ne 239 -or $bytes[1] -ne 187 -or $bytes[2] -ne 191) {
            throw ('Windows PowerShell source is missing its UTF-8 BOM: '+$file.Name)
        }
    }
    Invoke-IsolatedCheck -Name 'build' -Script (Join-Path $source 'build.ps1')
    Invoke-IsolatedCheck -Name 'panel' -Script (Join-Path $source 'check-panel.ps1') -CountPattern 'CHECKS_PASSED=(\d+)' -MinimumCount 49
    Invoke-IsolatedCheck -Name 'switch-recovery' -Script (Join-Path $source 'check-switch-recovery.ps1') -CountPattern 'SWITCH_RECOVERY_CHECKS_PASSED=(\d+)' -MinimumCount 29
    Invoke-IsolatedCheck -Name 'stcp-safety' -Script (Join-Path $source 'check-stcp-safety.ps1') -CountPattern 'STCP_SAFETY_CHECKS_PASSED=(\d+)'
    Invoke-IsolatedCheck -Name 'ui-regression' -Script (Join-Path $source 'check-ui-fixes.ps1') -Arguments @('-Executable',(Join-Path $source 'LocalAI-Control.exe')) -CountPattern '"Passed"\s*:\s*(\d+)' -MinimumCount 19
    Invoke-IsolatedCheck -Name 'offline-preview' -Script (Join-Path $PSScriptRoot 'Render-ConsolePreview.ps1') -Arguments @('-Executable',(Join-Path $source 'LocalAI-Control.exe'),'-OutputDirectory',$output)
    $success = $true
} finally {
    $report = [pscustomobject]@{
        Schema=1
        Commit=$env:GITHUB_SHA
        Platform=[Environment]::OSVersion.VersionString
        PowerShell=$PSVersionTable.PSVersion.ToString()
        Success=$success
        Results=@($results)
        Scope='Native Windows build and isolated fixtures only. No real Ollama, frpc, VPS or user configuration was accessed.'
        NotTested=@('Live model inference','Real multi-VPS switching','Installed Windows application upgrade','User desktop screenshot','Signing and SmartScreen')
    }
    [IO.File]::WriteAllText((Join-Path $output 'native-checks.json'),($report|ConvertTo-Json -Depth 6),$utf8)
}
