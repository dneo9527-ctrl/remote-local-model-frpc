param([string]$Executable)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
if ([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') { throw 'Run this WPF component check using Windows PowerShell -STA.' }
$testFolder=Join-Path $PSScriptRoot 'ui-regression'
[void][IO.Directory]::CreateDirectory($testFolder)
if (-not $Executable) {
    $Executable=Join-Path $testFolder ('LocalAI-Control.Layout-'+[guid]::NewGuid().ToString('N')+'.exe')
    $framework=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319'
    $references=@('System.dll','System.Core.dll','System.Web.Extensions.dll','System.Xaml.dll',
        (Join-Path $framework 'WPF\WindowsBase.dll'),(Join-Path $framework 'WPF\PresentationCore.dll'),(Join-Path $framework 'WPF\PresentationFramework.dll'))
    $arguments=@('/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001',('/out:'+$Executable),
        ('/resource:'+(Join-Path $PSScriptRoot 'LocalAI-GUI.ps1')+',LocalAI.Backend.ps1'),
        ('/resource:'+(Join-Path $PSScriptRoot 'LocalAI.UI.xaml')+',LocalAI.UI.xaml'),
        ('/resource:'+(Join-Path $PSScriptRoot 'scripts\start-services.ps1')+',LocalAI.StartServices.ps1'))
    foreach($reference in $references){$arguments+=('/reference:'+$reference)}
    foreach($flag in Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'flags') -Filter '*.png' -File){$arguments+=('/resource:'+$flag.FullName+',LocalAI.Flag.'+$flag.Name)}
    foreach($file in @('LocalAI-Control.cs','PanelConfig.cs','PanelExport.cs','PanelWindows.cs')){$arguments+=(Join-Path $PSScriptRoot $file)}
    & (Join-Path $framework 'csc.exe') @arguments
    if($LASTEXITCODE -ne 0){throw 'Layout/settings component build failed'}
}
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.Web.Extensions
$assembly=[Reflection.Assembly]::LoadFrom([IO.Path]::GetFullPath($Executable))
$panelType=$assembly.GetType('LocalAIControl.Panel',$true)
$prefType=$assembly.GetType('LocalAIControl.PanelPreferences',$true)
$instanceFlags=[Reflection.BindingFlags]'NonPublic,Instance'
$checks=[Collections.Generic.List[string]]::new()
function Check([bool]$Value,[string]$Name){if(-not $Value){throw ('FAILED: '+$Name)};$checks.Add($Name)}
function Read-Pref([string]$Path){return $prefType.GetMethod('Read').Invoke($null,[object[]]@($Path))}
function Bytes-Pref([string]$Path){return ,$prefType.GetMethod('Bytes').Invoke($null,[object[]]@($Path))}
function Write-Pref([string]$Path,[byte[]]$Expected,[byte[]]$Data,[string]$Backup){[void]$prefType.GetMethod('Write').Invoke($null,[object[]]@($Path,$Expected,$Data,$Backup))}
function Url-Pref([string]$Value){return $prefType.GetMethod('WebUrl').Invoke($null,[object[]]@($Value))}
function Port-Pref($Values,[string]$Key,[int]$Default){return $prefType.GetMethod('Port').Invoke($null,[object[]]@($Values,$Key,$Default))}
function Bytes-Call([string]$Method,[byte[]]$Data){$arguments=[object[]]::new(1);$arguments[0]=$Data;return $prefType.GetMethod($Method).Invoke($null,$arguments)}
function Refused([scriptblock]$Operation){try{[void](& $Operation);return $false}catch{return $true}}
function Find-Logical($Start,[type]$OfType){
    if($OfType.IsInstanceOfType($Start)){Write-Output -NoEnumerate $Start}
    foreach($item in [Windows.LogicalTreeHelper]::GetChildren($Start)) { if($item -is [Windows.DependencyObject]) {Find-Logical $item $OfType} }
}
function Child-Of($Child,$Ancestor){
    $current=$Child
    while($null -ne $current){if([object]::ReferenceEquals($current,$Ancestor)){return $true};$current=[Windows.LogicalTreeHelper]::GetParent($current)}
    return $false
}

$root=Join-Path $testFolder ('layout-settings-fixture-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory((Join-Path $root 'config'))
$utf8=[Text.UTF8Encoding]::new($false)
$privateConfig=Join-Path $root 'config\frpc.toml'
[IO.File]::WriteAllText($privateConfig,"serverAddr = '192.0.2.10'`nserverPort = 17500`n[[proxies]]`nname = 'ollama'`ntype = 'stcp'`nlocalPort = 12580`n",$utf8)
$protectedHash=(Get-FileHash -LiteralPath $privateConfig).Hash
$prefPath=Join-Path $root 'config\panel-preferences.json'
[IO.File]::WriteAllText($prefPath,'{"allowExternalMetadata":true,"ollamaPort":12580,"visitorPort":12581,"customNote":"keep-me"}',$utf8)
$panel=$panelType.GetConstructor([type[]]@([string])).Invoke([object[]]@([string]$root))
$popups=[Collections.Generic.List[Windows.Window]]::new()
try {
    $window=$panel.Window
    Check (-not $window.IsVisible) 'constructing the panel does not display or start service actions'
    $layout=$window.Content
    $header=@($layout.Children | Where-Object {[Windows.Controls.Grid]::GetRow($_) -eq 0})
    Check ($header.Count -eq 1 -and (Child-Of ($window.FindName('StartButton')) $header[0])) 'main start button is inside the fixed header'
    Check (($window.FindName('StartButton')).Height -eq 40) 'header action has the intended compact primary size'
    $cardGrid=@(Find-Logical $layout ([Windows.Controls.Primitives.UniformGrid]) | Where-Object {$_.Columns -eq 3})
    Check ($cardGrid.Count -eq 1) 'the three service cards remain one comparable group'
    Check ((Child-Of ($window.FindName('StopOllamaButton')) $cardGrid[0].Children[0]) -and (Child-Of ($window.FindName('StopButton')) $cardGrid[0].Children[1])) 'model and frp stop controls belong to their own cards'
    $thirdButtons=@(Find-Logical $cardGrid[0].Children[2] ([Windows.Controls.Button]))
    Check ($thirdButtons.Count -eq 0) 'Tailscale card has no accidental global disconnect action'
    $footer=@($layout.Children | Where-Object {[Windows.Controls.Grid]::GetRow($_) -eq 2})
    Check ($footer.Count -eq 1 -and (Child-Of ($window.FindName('ConnectionButton')) $footer[0]) -and (Child-Of ($window.FindName('HistoryButton')) $footer[0]) -and (Child-Of ($window.FindName('SettingsButton')) $footer[0])) 'connection history and settings share the fixed footer'
    Check (($window.FindName('CherryButton')).Height -le 32 -and ($window.FindName('WebButton')).Height -le 32) 'client and web entries are secondary compact controls'
    Check (($window.FindName('History')).Visibility -eq [Windows.Visibility]::Collapsed) 'main panel does not dedicate height to the internal history buffer'

    $list=$window.FindName('RouteList')
    $route=$list.Items[0]
    $template=$list.ItemTemplate.LoadContent()
    $template.DataContext=$route
    $template.Measure([Windows.Size]::new(850,80));$template.Arrange([Windows.Rect]::new(0,0,850,80));$template.UpdateLayout()
    $exportButtons=@(Find-Logical $template ([Windows.Controls.Button]) | Where-Object {$_.Content -eq '复制配置'})
    Check ($exportButtons.Count -eq 1 -and [Windows.Controls.Grid]::GetColumn($exportButtons[0]) -eq 4) 'each actual route template has a right-edge export button'
    Check ([object]::ReferenceEquals($exportButtons[0].Tag,$route)) 'export button is bound to its own route rather than current selection'
    Check ([Windows.Automation.AutomationProperties]::GetName($exportButtons[0]) -eq '复制此线路配置') 'per-route export has an accessible purpose'
    $store=[Windows.UIElement].GetProperty('EventHandlersStore',$instanceFlags).GetValue($list,$null)
    $handlers=$store.GetType().GetMethod('GetRoutedEventHandlers',[Reflection.BindingFlags]'Public,NonPublic,Instance').Invoke($store,[object[]]@([Windows.Controls.Button]::ClickEvent))
    Check ($handlers.Count -ge 1) 'route list owns a bubbling click handler for export buttons'
    # Inspect the registered handler's call tokens; do not execute export or open a save dialog.
    $callsExport=$false
    foreach($info in $handlers){
        $method=$info.Handler.Method;$il=$method.GetMethodBody().GetILAsByteArray()
        for($i=0;$i -lt $il.Length-4;$i++){
            if($il[$i] -notin @(0x28,0x6f)){continue}
            try{$called=$method.Module.ResolveMethod([BitConverter]::ToInt32($il,$i+1));if($called.Name -eq 'ExportRoute'){$callsExport=$true}}catch{}
        }
    }
    Check $callsExport 'registered route click handler dispatches to ExportRoute without executing it'

    # An owned WPF window needs its owner's HWND. EnsureHandle creates it hidden,
    # without Show/ContentRendered, so no automatic refresh or backend task runs.
    [void]([Windows.Interop.WindowInteropHelper]::new($window)).EnsureHandle()
    $smallWindow=$panelType.GetMethod('SmallWindow',$instanceFlags)
    foreach($title in @('连接信息','日志与操作记录','设置 · 端口与客户端')) {
        $dialog=$smallWindow.Invoke($panel,[object[]]@($title,420));$popups.Add($dialog)
        Check ($dialog.Title -eq $title -and [object]::ReferenceEquals($dialog.Owner,$window) -and -not $dialog.IsVisible -and $dialog.Width -ge 500) ($title+' popup frame can be constructed without a modal event loop')
    }
    foreach($methodName in @('ConnectionWindow','HistoryWindow','SettingsWindow')){Check ($null -ne $panelType.GetMethod($methodName,$instanceFlags)) ($methodName+' entry is present')}
    $paragraph=$panelType.GetMethod('Paragraph',$instanceFlags).Invoke($panel,[object[]]@('fixture paragraph',$true))
    $smallButton=$panelType.GetMethod('SmallButton',$instanceFlags).Invoke($panel,[object[]]@('fixture action'))
    Check ($paragraph.TextWrapping -eq [Windows.TextWrapping]::Wrap -and $smallButton.MinHeight -ge 32) 'popup shared content components construct with readable wrapping and controls'

    # Match the backend's actual OS mutex across processes, not only in-memory state.
    $operationType=$assembly.GetType('LocalAIControl.PanelOperationLock',$true)
    $operation=[Activator]::CreateInstance($operationType)
    try {
        $probe=Join-Path $testFolder 'operation-lock-probe.ps1'
        [IO.File]::WriteAllText($probe,'$m=[Threading.Mutex]::new($false,("Local\LocalAIControl-"+$env:USERNAME));try{if($m.WaitOne(0)){$m.ReleaseMutex();exit 99}else{exit 23}}finally{$m.Dispose()}',[Text.UTF8Encoding]::new($true))
        & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -File $probe
        $exit=$LASTEXITCODE;$global:LASTEXITCODE=0
        Check ($exit -eq 23) 'settings save holds the same cross-process operation mutex as service start and stop'
    } finally {$operation.Dispose()}
    $operation=[Activator]::CreateInstance($operationType);$operation.Dispose()
    Check $true 'settings operation lock is released after disposal'

    $values=Read-Pref $prefPath
    Check ((Port-Pref $values 'ollamaPort' 11434) -eq 12580 -and (Port-Pref $values 'visitorPort' 11436) -eq 12581) 'stored model and visitor ports read back exactly'
    Check ((Port-Pref $values 'missing' 11436) -eq 11436) 'absent optional port retains its documented default'
    $old=Bytes-Pref $prefPath
    $values['ollamaPort']=12582;$values['webChatUrl']=Url-Pref 'http://127.0.0.1:8080/chat'
    $encoded=$utf8.GetBytes(([Web.Script.Serialization.JavaScriptSerializer]::new()).Serialize($values))
    Write-Pref $prefPath $old $encoded ($prefPath+'.previous')
    $again=Read-Pref $prefPath
    Check ($again['allowExternalMetadata'] -eq $true -and $again['customNote'] -eq 'keep-me' -and $again['ollamaPort'] -eq 12582) 'saving known preferences retains metadata opt-in and unrelated existing values'
    Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($prefPath+'.previous')) -ceq [Convert]::ToBase64String($old)) 'preference replacement keeps exact prior bytes in the local recovery file'
    Check ((Refused {Write-Pref $prefPath $old $utf8.GetBytes('{}') ($prefPath+'.previous')})) 'stale expected preference snapshot is refused'
    Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($prefPath)) -ceq [Convert]::ToBase64String($encoded)) 'snapshot conflict preserves the newer preferences'
    foreach($value in @('0','65536','"8080"','1.5','true','null')) {
        $invalid=([Web.Script.Serialization.JavaScriptSerializer]::new()).DeserializeObject(('{"ollamaPort":'+$value+'}'))
        $dictionary=[Collections.Generic.Dictionary[string,object]]::new();$dictionary['ollamaPort']=$invalid['ollamaPort']
        Check ((Refused {Port-Pref $dictionary 'ollamaPort' 11434})) ('invalid port '+$value+' is refused rather than coerced')
    }
    Check ((Url-Pref ' https://example.org/chat ') -eq 'https://example.org/chat' -and (Url-Pref '') -eq '') 'web URL accepts explicit HTTP(S) and an intentionally unset entry'
    foreach($url in @('file:///tmp/chat','javascript:alert(1)','http://name:password@example.org','http://localhost:99999','http://localhost:0',"https://example.org/`nchat")) {
        Check ((Refused {Url-Pref $url})) 'unsafe or malformed web URL is refused'
    }
    Check ((Get-FileHash -LiteralPath $privateConfig).Hash -ceq $protectedHash) 'all layout and preference checks leave the fixture frpc config unchanged'
    Check (@(Get-ChildItem -LiteralPath (Split-Path -Parent $prefPath) -Filter '*.tmp' -File).Count -eq 0) 'preference writes leave no staging files'
    $captured=$utf8.GetBytes('{"ollamaPort":12582,"allowExternalMetadata":false}')
    $capturedValue=Bytes-Call 'ReadSnapshot' $captured
    Check ($capturedValue['ollamaPort'] -eq 12582 -and $capturedValue['allowExternalMetadata'] -eq $false) 'preference snapshot parses exactly the captured bytes'
    [IO.File]::WriteAllText($prefPath,'{"ollamaPort":12600}',$utf8)
    Check ($capturedValue['ollamaPort'] -eq 12582) 'later file changes do not change an already-read preference snapshot'
    $bom=[byte[]](@(239,187,191)+@($captured))
    Check ((Bytes-Call 'ReadSnapshot' $bom)['ollamaPort'] -eq 12582) 'preference snapshot accepts a UTF8 BOM without a second file read'
    Check ((Bytes-Call 'ReadSnapshot' $null).Count -eq 0) 'missing preference snapshot creates empty defaults'
    Check ((Refused {Bytes-Call 'ReadSnapshot' ([byte[]]@(255,254,255))})) 'invalid UTF8 preference bytes are refused'

    # Interrupted two-file settings transactions use separate fixture trees.
    $beforeConfig=$utf8.GetBytes("serverPort = 17500`n")
    $afterConfig=$utf8.GetBytes("serverPort = 17501`n")
    $beforePrefs=$utf8.GetBytes('{"ollamaPort":12580}')
    $afterPrefs=$utf8.GetBytes('{"ollamaPort":12582}')
    $hashBeforeConfig=Bytes-Call 'Hash' $beforeConfig
    $hashAfterConfig=Bytes-Call 'Hash' $afterConfig
    $hashBeforePrefs=Bytes-Call 'Hash' $beforePrefs
    $hashAfterPrefs=Bytes-Call 'Hash' $afterPrefs
    $plan=@{configBefore=$hashBeforeConfig;configAfter=$hashAfterConfig;prefsBefore=$hashBeforePrefs;prefsAfter=$hashAfterPrefs}|ConvertTo-Json -Compress
    $recover=$prefType.GetMethod('RecoverPorts')
    foreach($case in @('before-before','after-after','after-before','external-config','bad-backup','before-after')) {
        $caseRoot=Join-Path $root $case;$caseFolder=Join-Path $caseRoot 'config'
        [void][IO.Directory]::CreateDirectory($caseFolder)
        $caseConfig=Join-Path $caseFolder 'frpc.toml';$casePrefs=Join-Path $caseFolder 'panel-preferences.json';$pending=Join-Path $caseFolder '.panel-port-change.pending.json'
        $configBytes=if($case -in @('before-before','before-after')){$beforeConfig}elseif($case -eq 'external-config'){$utf8.GetBytes('# independent external edit')}else{$afterConfig}
        $prefsBytes=if($case -in @('after-after','before-after')){$afterPrefs}else{$beforePrefs}
        [IO.File]::WriteAllBytes($caseConfig,$configBytes);[IO.File]::WriteAllBytes($casePrefs,$prefsBytes)
        [IO.File]::WriteAllText($pending,$plan,$utf8)
        $backup=if($case -eq 'bad-backup'){$utf8.GetBytes('# unrelated backup')}else{$beforeConfig}
        [IO.File]::WriteAllBytes(($caseConfig+'.port-previous.bak'),$backup)
        if($case -in @('external-config','bad-backup','before-after')) {
            Check ((Refused {[void]$recover.Invoke($null,[object[]]@([string]$caseRoot))}) -and (Test-Path -LiteralPath $pending)) ($case+' refuses unsafe recovery and retains the journal')
            Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($caseConfig)) -ceq [Convert]::ToBase64String($configBytes) -and [Convert]::ToBase64String([IO.File]::ReadAllBytes($casePrefs)) -ceq [Convert]::ToBase64String($prefsBytes)) ($case+' preserves both files exactly')
        } else {
            [void]$recover.Invoke($null,[object[]]@([string]$caseRoot))
            $expectedConfig=if($case -eq 'after-after'){$afterConfig}else{$beforeConfig}
            Check (-not (Test-Path -LiteralPath $pending)) ($case+' resolves the interrupted transaction journal')
            Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($caseConfig)) -ceq [Convert]::ToBase64String($expectedConfig) -and [Convert]::ToBase64String([IO.File]::ReadAllBytes($casePrefs)) -ceq [Convert]::ToBase64String($prefsBytes)) ($case+' leaves a consistent file pair without altering credentials or unrelated fields')
        }
    }
} finally {foreach($dialog in $popups){$dialog.Close()};$panel.Window.Close()}
$checks | ForEach-Object {'PASS '+$_}
'LAYOUT_SETTINGS_CHECKS_PASSED='+$checks.Count
'SCOPE=Hidden WPF component construction and private file fixtures; no ShowDialog, HTTP, model, shell launch or service actions.'
