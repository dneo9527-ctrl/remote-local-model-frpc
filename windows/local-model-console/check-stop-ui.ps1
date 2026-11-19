param([Parameter(Mandatory=$true)][string]$Executable)
$ErrorActionPreference='Stop'
if([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA'){throw 'Use Windows PowerShell -STA'}
Add-Type -AssemblyName PresentationFramework
$assembly=[Reflection.Assembly]::LoadFrom([IO.Path]::GetFullPath($Executable));$type=$assembly.GetType('LocalAIControl.Panel',$true)
$flags=[Reflection.BindingFlags]'NonPublic,Instance';$checks=[Collections.Generic.List[string]]::new()
function Check([bool]$Value,[string]$Name){if(-not $Value){throw ('FAILED: '+$Name)};$checks.Add($Name)}
$root=Join-Path ([IO.Path]::GetTempPath()) ('localai-stop-ui-'+[guid]::NewGuid().ToString('N'));[void][IO.Directory]::CreateDirectory((Join-Path $root 'config'))
[IO.File]::WriteAllText((Join-Path $root 'config\frpc.toml'),"serverAddr='192.0.2.10'`nserverPort=7000`n",[Text.UTF8Encoding]::new($false))
$panel=$type.GetConstructor([type[]]@([string])).Invoke([object[]]@([string]$root));$window=$panel.Window;$apply=$type.GetMethod('Apply',$flags)
$state=[Collections.Generic.Dictionary[string,object]]::new();$tunnel=[Collections.Generic.Dictionary[string,object]]::new();$tunnel['State']='ready';$tunnel['Text']='registered';$tunnel['Detail']='fixture'
$state['OllamaOnline']=$true;$state['OllamaCanStop']=$true;$state['OllamaListening']=$true;$state['ModelInstalled']=$true;$state['OllamaPort']=11434;$state['FrpcCount']=1;$state['Server']='192.0.2.10';$state['Model']='fixture-model';$state['Tunnel']=$tunnel
$dialog=$null
try{
 [void]$apply.Invoke($panel,[object[]]@($state))
 Check ($window.FindName('StartButton').Content -eq '一键停止') 'running services change the primary label to one-click stop'
 Check ($type.GetProperty('PrimaryActionName',$flags).GetValue($panel,$null) -eq 'StopAll') 'running primary action dispatches StopAll rather than Start'
 Check ($window.FindName('ResumeButton').Visibility -eq 'Collapsed') 'fully running services do not offer redundant resume action'
 Check ($window.FindName('StopOllamaButton').Background.Color.A -eq 0 -and $window.FindName('StopButton').BorderBrush.Color.A -eq 0) 'local stop controls use neutral borderless styling'
 $window.FindName('RouteList').SelectedIndex=1
 Check ($window.FindName('StartButton').Content -eq '应用并切线' -and $type.GetProperty('PrimaryActionName',$flags).GetValue($panel,$null) -eq 'Start') 'an unapplied route explicitly changes primary action to apply'
 Check ($window.FindName('GlobalStopButton').Visibility -eq 'Visible') 'current services remain stoppable while another route is selected'
 [void]$apply.Invoke($panel,[object[]]@($state))
 Check ($window.FindName('StartButton').Content -eq '应用并切线' -and $window.FindName('BannerText').Text.Contains('尚未应用')) 'polling preserves pending route intent'
 $window.FindName('RouteList').SelectedIndex=0
 $state['FrpcCount']=0;[void]$apply.Invoke($panel,[object[]]@($state))
 Check ($window.FindName('StartButton').Content -eq '一键停止' -and $window.FindName('ResumeButton').Visibility -eq 'Visible') 'model-only partial state offers stop and a separate resume action'
 Check ($window.FindName('StopButton').Visibility -eq 'Hidden') 'stopped tunnel does not show an inactive stop box'
 $state['FrpcCount']=1;$state['OllamaOnline']=$false;$state['OllamaCanStop']=$false;$state['OllamaListening']=$false;[void]$apply.Invoke($panel,[object[]]@($state))
 Check ($window.FindName('StartButton').Content -eq '一键停止' -and $window.FindName('ResumeButton').Visibility -eq 'Visible') 'tunnel-only partial state keeps the same stop/resume rule'
 $state['FrpcCount']=0;[void]$apply.Invoke($panel,[object[]]@($state))
 Check ($window.FindName('StartButton').Content -eq '一键启动' -and $window.FindName('StopOllamaButton').Visibility -eq 'Hidden') 'fully stopped services restore one-click start'
 $state['OllamaListening']=$true;[void]$apply.Invoke($panel,[object[]]@($state))
 Check ($window.FindName('StartButton').Content -eq '一键停止' -and $window.FindName('OllamaState').Text -eq '待检查') 'unhealthy listener is not falsely presented as fully stopped'
 [void]([Windows.Interop.WindowInteropHelper]::new($window)).EnsureHandle()
 $dialog=$type.GetMethod('BuildStopDialog',$flags).Invoke($panel,[object[]]@('StopAll'))
 Check (-not $dialog.IsVisible -and [object]::ReferenceEquals($dialog.Owner,$window)) 'matching confirmation constructs as an owned hidden WPF window'
 Check ($dialog.FindName('CancelStopButton').IsDefault -and $dialog.FindName('CancelStopButton').IsCancel) 'confirmation defaults Enter and Escape to cancellation'
 Check (-not $dialog.FindName('ConfirmStopButton').IsDefault -and $dialog.FindName('ConfirmStopButton').Content -eq '停止服务') 'destructive confirmation requires deliberate activation'
 Check ($dialog.ResizeMode -eq 'NoResize' -and $dialog.Width -le 500) 'stop confirmation uses a compact consistent frame'
}finally{if($dialog){$dialog.Close()};$window.Close()}
$checks|ForEach-Object {'PASS '+$_}
'STOP_UI_CHECKS_PASSED='+$checks.Count
'SCOPE=Hidden WPF states and dialog construction. No clicks, service actions, HTTP or production settings.'
