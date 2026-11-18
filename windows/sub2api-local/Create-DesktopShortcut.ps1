$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) { throw 'Windows Desktop directory could not be located.' }
$shortcutPath = Join-Path $desktop (([char]0x542F).ToString() + [char]0x52A8 + ' Sub2API.lnk')
$target = Join-Path $PSScriptRoot 'Start-Sub2API.cmd'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.TargetPath -ne $target) {
    throw 'A shortcut with this name belongs to another directory; it was kept.'
}
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = 'Start the Sub2API Docker project including its private frpc tunnel.'
$shortcut.WindowStyle = 1
$icon = Join-Path $env:ProgramFiles 'Docker/Docker/Docker Desktop.exe'
if (Test-Path -LiteralPath $icon) { $shortcut.IconLocation = $icon + ',0' }
$shortcut.Save()
Write-Host ('Created desktop shortcut: ' + $shortcutPath)
