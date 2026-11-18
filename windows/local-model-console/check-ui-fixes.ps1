param([string]$Executable)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$testFolder=Join-Path $PSScriptRoot 'ui-regression'
[void][IO.Directory]::CreateDirectory($testFolder)
if (-not $Executable) {
    $Executable=Join-Path $testFolder 'LocalAI-Control.UI-check.exe'
    $framework=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319'
    $references=@('System.dll','System.Core.dll','System.Web.Extensions.dll','System.Xaml.dll',
        (Join-Path $framework 'WPF\WindowsBase.dll'),(Join-Path $framework 'WPF\PresentationCore.dll'),(Join-Path $framework 'WPF\PresentationFramework.dll'))
    $arguments=@('/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001',('/out:'+$Executable),
        ('/resource:'+(Join-Path $PSScriptRoot 'LocalAI-GUI.ps1')+',LocalAI.Backend.ps1'),
        ('/resource:'+(Join-Path $PSScriptRoot 'LocalAI.UI.xaml')+',LocalAI.UI.xaml'),
        ('/resource:'+(Join-Path $PSScriptRoot 'scripts\start-services.ps1')+',LocalAI.StartServices.ps1'))
    foreach($reference in $references){$arguments+=('/reference:'+$reference)}
    foreach($flag in Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'flags') -Filter '*.png' -File){$arguments+=('/resource:'+$flag.FullName+',LocalAI.Flag.'+$flag.Name)}
    $arguments+=(Join-Path $PSScriptRoot 'LocalAI-Control.cs')
    foreach($extra in @('PanelConfig.cs','PanelExport.cs','PanelWindows.cs')){$arguments+=(Join-Path $PSScriptRoot $extra)}
    & (Join-Path $framework 'csc.exe') @arguments
    if($LASTEXITCODE -ne 0){throw 'UI regression build failed'}
}
Add-Type -AssemblyName PresentationFramework
$assembly=[Reflection.Assembly]::LoadFrom([IO.Path]::GetFullPath($Executable))
$type=$assembly.GetType('LocalAIControl.Panel')
$constructor=$type.GetConstructor([type[]]@([string]))
$flags=[Reflection.BindingFlags]'NonPublic,Instance'
$save=$type.GetMethod('SaveRoutes',$flags)
$apply=$type.GetMethod('Apply',$flags)
$checks=[Collections.Generic.List[string]]::new()
function Assert-Check([string]$Name,[bool]$Condition) {
    if(-not $Condition){throw ('FAILED: '+$Name)}
    $checks.Add($Name)
}
function New-FixtureRoot {
    $path=Join-Path $testFolder ('fixture-'+[guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory((Join-Path $path 'config'))
    return $path
}
function Load-Panel([string]$Path) {return $constructor.Invoke([object[]]@([string]$Path))}
function Save-Panel($Panel,[bool]$Manual) {[void]$save.Invoke($Panel,[object[]]@($Manual))}
function Apply-State($Panel,$Value) {[void]$apply.Invoke($Panel,[object[]]@($Value))}
$root=New-FixtureRoot
$config=Join-Path $root 'config\panel-routes.json'
$protected=Join-Path $root 'config\frpc.toml'
[IO.File]::WriteAllText($protected,'fixture protected configuration',[Text.UTF8Encoding]::new($false))
$protectedHash=(Get-FileHash -LiteralPath $protected).Hash
$panel=Load-Panel $root
try {
    Assert-Check 'external IP metadata defaults off' (-not $type.GetField('externalMetadataEnabled',$flags).GetValue($panel))
    Assert-Check 'update entry is present' ($null -ne $panel.Window.FindName('UpdateButton'))
    $embeddedStart=$type.GetField('startScript',$flags).GetValue($panel)
    Assert-Check 'current release extracts its STCP helper outside source' ((Test-Path -LiteralPath $embeddedStart) -and $embeddedStart.StartsWith($root) -and [IO.File]::ReadAllText($embeddedStart).Contains('Ensure-LocalOllama'))
    $probePort=$type.GetMethod('ConfiguredProbePort',$flags)
    [IO.File]::WriteAllText($protected,"serverAddr = '192.0.2.10'`nserverPort = 17443 # configured port`n[[proxies]]`nserverPort = 9000",[Text.UTF8Encoding]::new($false))
    Assert-Check 'TCP probe reads the configured top-level frps port' ($probePort.Invoke($panel,@()) -eq 17443)
    [IO.File]::WriteAllText($protected,"serverPort = 7000`nserverPort = 17443",[Text.UTF8Encoding]::new($false))
    Assert-Check 'ambiguous probe port refuses arbitrary fallback' ($probePort.Invoke($panel,@()) -eq 0)
    [IO.File]::WriteAllText($protected,"serverPort = 'not-an-integer'",[Text.UTF8Encoding]::new($false))
    Assert-Check 'invalid explicit probe port is not replaced by 7000' ($probePort.Invoke($panel,@()) -eq 0)
    [IO.File]::WriteAllText($protected,'fixture protected configuration',[Text.UTF8Encoding]::new($false))
    $routes=$type.GetField('routes',$flags).GetValue($panel)
    $list=$panel.Window.FindName('RouteList')
    Assert-Check 'initial routes created without a fictitious manual backup' ((Test-Path -LiteralPath $config) -and -not (Test-Path -LiteralPath ($config+'.previous')))
    $deleted=$routes[1].Address
    $routes.RemoveAt(1)
    Save-Panel $panel $true
    $manualBackup=[IO.File]::ReadAllText($config+'.previous')
    Assert-Check 'manual deletion keeps deleted route in recovery copy' ($manualBackup.Contains($deleted) -and -not [IO.File]::ReadAllText($config).Contains($deleted))
    $routes[0].GeoCode='US';$routes[0].GeoCity='FixtureCity';$routes[0].GeoCheckedAt=[DateTime]::UtcNow.ToString('o')
    Save-Panel $panel $false
    Assert-Check 'changed probe cache saves without rotating manual backup' ([IO.File]::ReadAllText($config).Contains('FixtureCity') -and [IO.File]::ReadAllText($config+'.previous') -ceq $manualBackup)
    Assert-Check 'probe save uses current collection and does not resurrect a deleted route' (-not [IO.File]::ReadAllText($config).Contains($deleted))
    $stamp=[DateTime]::UtcNow.AddHours(-2)
    [IO.File]::SetLastWriteTimeUtc($config,$stamp)
    $beforeStamp=[IO.File]::GetLastWriteTimeUtc($config)
    Save-Panel $panel $false
    Assert-Check 'unchanged automatic save does not rewrite the list' ([IO.File]::GetLastWriteTimeUtc($config) -eq $beforeStamp)
    $beforeNextEdit=[IO.File]::ReadAllText($config)
    $routes[0].Name='LineA'
    Save-Panel $panel $true
    Assert-Check 'next manual edit advances the manual recovery point' ([IO.File]::ReadAllText($config+'.previous') -ceq $beforeNextEdit)
    $beforeFailure=[IO.File]::ReadAllText($config)
    $backupBeforeFailure=[IO.File]::ReadAllText($config+'.previous')
    $lock=[IO.File]::Open($config,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    $routes[0].Name='unsaved-locked-change';$saveFailed=$false
    try {Save-Panel $panel $true}catch{$saveFailed=$true}finally{$lock.Dispose()}
    Assert-Check 'failed save preserves both current list and manual backup' ($saveFailed -and [IO.File]::ReadAllText($config) -ceq $beforeFailure -and [IO.File]::ReadAllText($config+'.previous') -ceq $backupBeforeFailure)
    Assert-Check 'failed save leaves no temporary file' (@(Get-ChildItem -LiteralPath (Split-Path $config -Parent) -Filter '*.tmp' -File).Count -eq 0)
    $routes[0].Name='LineA';$routes[1].Name='LineB'
    $tunnel=[Collections.Generic.Dictionary[string,object]]::new()
    $tunnel['State']='ready';$tunnel['Text']='registered';$tunnel['Detail']='fixture'
    $state=[Collections.Generic.Dictionary[string,object]]::new()
    $state['OllamaOnline']=$true;$state['ModelInstalled']=$true;$state['TailscaleConnected']=$false
    $state['Tunnel']=$tunnel;$state['FrpcCount']=1;$state['Server']=$routes[0].Address;$state['Model']='fixture-model'
    Apply-State $panel $state
    $readyHeader=$panel.Window.FindName('HeaderState').Text
    $readyBanner=$panel.Window.FindName('BannerText').Text
    $keepButton=$panel.Window.FindName('StartButton').Content
    $list.SelectedIndex=1
    $pending=$panel.Window.FindName('BannerText').Text
    Apply-State $panel $state
    Apply-State $panel $state
    Assert-Check 'pending selection survives repeated refreshes and names both lines' ($pending -ceq $panel.Window.FindName('BannerText').Text -and $pending.Contains('LineA') -and $pending.Contains('LineB') -and $pending -cne $readyBanner)
    $list.SelectedIndex=0
    Assert-Check 'returning to the saved line immediately clears pending hint' ($panel.Window.FindName('BannerText').Text -ceq $readyBanner)
    $routes[0].Address='HK.Node.Example.com';$state['Server']='hk.node.example.com'
    Apply-State $panel $state
    Assert-Check 'case-insensitive host match keeps normal start button' ($panel.Window.FindName('StartButton').Content -ceq $keepButton)
    Assert-Check 'case-insensitive host match resolves saved route name' ($panel.Window.FindName('CurrentRoute').Text.EndsWith('LineA'))
    Assert-Check 'case-insensitive host match does not show pending line hint' (-not $panel.Window.FindName('BannerText').Text.Contains('LineB'))
    $state['ModelInstalled']=$false;$state['Model']='fixture-missing-model'
    Apply-State $panel $state
    Assert-Check 'missing default model changes ready header' ($panel.Window.FindName('HeaderState').Text -cne $readyHeader)
    Assert-Check 'missing default model is named in status banner' ($panel.Window.FindName('BannerText').Text.Contains('fixture-missing-model') -and $panel.Window.FindName('BannerText').Text -cne $readyBanner)
    $state['ModelInstalled']=$true;$state['OllamaOnline']=$false
    Apply-State $panel $state
    Assert-Check 'registered tunnel alone never claims model ready' ($panel.Window.FindName('HeaderState').Text -cne $readyHeader -and $panel.Window.FindName('BannerText').Text -cne $readyBanner)
    Assert-Check 'UI regression leaves frpc configuration untouched' ((Get-FileHash -LiteralPath $protected).Hash -eq $protectedHash)
}finally{$panel.Window.Close()}
foreach($withBackup in @($false,$true)) {
    $badRoot=New-FixtureRoot
    $badPath=Join-Path $badRoot 'config\panel-routes.json'
    [IO.File]::WriteAllText($badPath,'{bad-json',[Text.UTF8Encoding]::new($false))
    if($withBackup){[IO.File]::WriteAllText($badPath+'.previous','fixture backup',[Text.UTF8Encoding]::new($false))}
    $loadError=''
    try {$unexpected=Load-Panel $badRoot;$unexpected.Window.Close()}catch{$errorObject=$_.Exception;while($errorObject.InnerException){$errorObject=$errorObject.InnerException};$loadError=$_.Exception.ToString()}
    Assert-Check ('corrupt list recovery advice matches backup existence '+$withBackup) ($loadError.Length -gt 0 -and $loadError.Contains('panel-routes.json.previous') -eq $withBackup -and [IO.File]::ReadAllText($badPath) -ceq '{bad-json')
}
$result=[pscustomobject]@{Passed=$checks.Count;Failed=0;Checks=$checks;Executable=$Executable;Scope='Hidden WPF components and isolated file fixtures only; no live model, tunnel or network calls.'}
$output=$result|ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $testFolder 'ui-checks.json'),$output,[Text.UTF8Encoding]::new($false))
$output
