param([string]$OutputDirectory = $PSScriptRoot)
$ErrorActionPreference='Stop'
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
[void][IO.Directory]::CreateDirectory($OutputDirectory)
$framework = Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319'
$compiler = Join-Path $framework 'csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '需要 Windows x64 内置 .NET Framework 4.x 编译器。' }
Add-Type -AssemblyName System.Drawing
$bitmap=[Drawing.Bitmap]::new(64,64);$g=[Drawing.Graphics]::FromImage($bitmap)
$g.SmoothingMode='AntiAlias';$g.Clear([Drawing.Color]::FromArgb(18,32,58))
$blue=[Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(37,99,235));$g.FillEllipse($blue,5,5,54,54)
$font=[Drawing.Font]::new('Segoe UI',24,[Drawing.FontStyle]::Bold,[Drawing.GraphicsUnit]::Pixel)
$g.DrawString('AI',$font,[Drawing.Brushes]::White,13,17)
$icon=[Drawing.Icon]::FromHandle($bitmap.GetHicon());$stream=[IO.File]::Create((Join-Path $OutputDirectory 'app.ico'))
$icon.Save($stream);$stream.Dispose();$icon.Dispose();$font.Dispose();$blue.Dispose();$g.Dispose();$bitmap.Dispose()
$references=@('System.dll','System.Core.dll','System.Web.Extensions.dll','System.Xaml.dll',
 (Join-Path $framework 'WPF\WindowsBase.dll'),(Join-Path $framework 'WPF\PresentationCore.dll'),(Join-Path $framework 'WPF\PresentationFramework.dll'))
$arguments=@('/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001',
 ('/out:'+(Join-Path $OutputDirectory 'LocalAI-Control.exe')),('/win32manifest:'+(Join-Path $PSScriptRoot 'app.manifest')),
 ('/win32icon:'+(Join-Path $OutputDirectory 'app.ico')),('/resource:'+(Join-Path $PSScriptRoot 'LocalAI-GUI.ps1')+',LocalAI.Backend.ps1'),
 ('/resource:'+(Join-Path $PSScriptRoot 'LocalAI.UI.xaml')+',LocalAI.UI.xaml'),
 ('/resource:'+(Join-Path $PSScriptRoot 'scripts\start-services.ps1')+',LocalAI.StartServices.ps1'))
foreach($reference in $references){$arguments+=('/reference:'+$reference)}
foreach($flag in Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'flags') -Filter '*.png' -File){$arguments+=('/resource:'+$flag.FullName+',LocalAI.Flag.'+$flag.Name)}
$arguments+=(Join-Path $PSScriptRoot 'LocalAI-Control.cs')
    foreach($extra in @('PanelConfig.cs','PanelExport.cs','PanelWindows.cs')){$arguments+=(Join-Path $PSScriptRoot $extra)}
& $compiler @arguments
if($LASTEXITCODE -ne 0){throw 'EXE 编译失败。'}
Get-Item -LiteralPath (Join-Path $OutputDirectory 'LocalAI-Control.exe') | Select-Object FullName,Length
