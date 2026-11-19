#requires -Version 5.1
# Explicit native acceptance with two disposable compiled processes, never real AI/frp binaries.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'LocalAI-GUI.ps1') -LibraryOnly
$checks=[Collections.Generic.List[string]]::new()
function Check([bool]$Value,[string]$Name){if(-not $Value){throw ('FAILED: '+$Name)};$checks.Add($Name)}
$root=Join-Path ([IO.Path]::GetTempPath()) ('localai-owned-processes-'+[guid]::NewGuid().ToString('N'))
foreach($dir in @('config','bin\frp','fixture-ollama','other')){[void][IO.Directory]::CreateDirectory((Join-Path $root $dir))}
$reservation=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0);$reservation.Start();$port=$reservation.LocalEndpoint.Port;$reservation.Stop()
$utf8=[Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText((Join-Path $root 'config\panel-preferences.json'),('{"ollamaPort":'+$port+'}'),$utf8)
[IO.File]::WriteAllText((Join-Path $root 'config\frpc.toml'),"serverAddr='192.0.2.10'`n",$utf8)
$code=@'
using System;using System.Net;using System.Net.Sockets;using System.Threading;
class FixtureOnly {static void Main(string[] args){TcpListener listener=null;if(args.Length>1&&args[0]=="serve"){listener=new TcpListener(IPAddress.Loopback,Int32.Parse(args[1]));listener.Start();}Thread.Sleep(Timeout.Infinite);}}
'@
$codePath=Join-Path $root 'FixtureOnly.cs';[IO.File]::WriteAllText($codePath,$code,$utf8)
$script:fixtureOllamaExe=Join-Path $root 'fixture-ollama\ollama.exe';$frpcExe=Join-Path $root 'bin\frp\bridge.exe';$otherExe=Join-Path $root 'other\bridge.exe'
$compiler=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:exe /platform:x64 ('/out:'+$script:fixtureOllamaExe) $codePath
if($LASTEXITCODE -ne 0){throw 'Fixture compiler failed'}
Copy-Item -LiteralPath $script:fixtureOllamaExe -Destination $frpcExe;Copy-Item -LiteralPath $script:fixtureOllamaExe -Destination $otherExe
function Get-OllamaExecutablePaths {@($script:fixtureOllamaExe)}
$model=$null;$tunnel=$null;$other=$null
try{
 $model=Start-Process -FilePath $script:fixtureOllamaExe -ArgumentList @('serve',[string]$port) -WindowStyle Hidden -PassThru;[void]$model.Handle
 $argsForFrpc=@('-c',('"'+(Join-Path $root 'config\frpc.toml')+'"'))
 $tunnel=Start-Process -FilePath $frpcExe -ArgumentList $argsForFrpc -WindowStyle Hidden -PassThru;[void]$tunnel.Handle
 $other=Start-Process -FilePath $otherExe -ArgumentList $argsForFrpc -WindowStyle Hidden -PassThru;[void]$other.Handle
 $ready=$false
 for($i=0;$i -lt 30;$i++){
  $snapshot=Get-OllamaStopSnapshot -Port $port
  if($snapshot.CanStop -and @(Get-OwnedFrpc $root).Count -eq 1){$ready=$true;break}
  Start-Sleep -Milliseconds 100
 }
 Check ($ready -and $snapshot.Process.ProcessId -eq $model.Id) 'actual CIM/listener ownership identifies only the disposable model fixture'
 Check (@(Get-OwnedFrpc $root)[0].ProcessId -eq $tunnel.Id) 'actual executable/config ownership selects only the disposable project tunnel'
 $message=Stop-LocalAiServices -Root $root
 Check ($model.WaitForExit(4000)) 'one stop operation terminates the owned native model fixture'
 Check ($tunnel.WaitForExit(4000)) 'one stop operation terminates the owned native tunnel fixture'
 $after=Get-OllamaStopSnapshot -Port $port
 Check ($after.QuerySucceeded -and -not $after.HasListener -and @(Get-OwnedFrpc $root).Count -eq 0) 'post-stop native listener/process checks confirm both components are gone'
 Check (-not $other.HasExited) 'an unrelated executable with the same process name and config argument survives'
 Check ($message.Contains('已停止')) 'success message is returned only after native exit verification'
}finally{
 # Only these held process handles may be cleaned up. No process-name cleanup.
 foreach($process in @($model,$tunnel,$other)){
  if($null -eq $process){continue}
  try{if(-not $process.HasExited){$path=$process.Path;if($path -in @($script:fixtureOllamaExe,$frpcExe,$otherExe)){$process.Kill();[void]$process.WaitForExit(4000)}}}finally{$process.Dispose()}
 }
}
$checks|ForEach-Object {'PASS '+$_}
'STOP_PROCESS_CHECKS_PASSED='+$checks.Count
'SCOPE=Real disposable native fixture processes and loopback-only ephemeral listener. No actual Ollama/frpc binaries or production settings.'
