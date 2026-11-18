$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'LocalAI-GUI.ps1') -LibraryOnly
$checks=[Collections.Generic.List[string]]::new()
function Check([bool]$Value,[string]$Name){if(-not $Value){throw ('FAILED: '+$Name)};$checks.Add($Name)}
function Reset-StopCase([string]$Mode,[int]$Frpc=1,[bool]$Model=$true){$script:mode=$Mode;$script:frpc=$Frpc;$script:model=$Model;$script:tunnelCalls=0;$script:modelCalls=0;$script:ports=@()}
function Get-LocalOllamaPort {param($Root) if($script:mode -eq 'invalid-port'){throw 'fixture invalid preferences'};12580}
function Stop-OwnedFrpc {param($Root) $script:tunnelCalls++;if($script:mode -eq 'tunnel-fail'){throw 'fixture tunnel refused'};$script:frpc=0;return 1}
function Get-OwnedFrpc {param($Root) if($script:mode -eq 'new-tunnel'){@([pscustomobject]@{ProcessId=101})}elseif($script:frpc -gt 0){@([pscustomobject]@{ProcessId=100})}}
function Get-OllamaStopSnapshot {
 param($Port)
 $script:ports+=@($Port)
 [pscustomobject]@{CanStop=($script:model -and $script:mode -ne 'unknown-model');HasListener=$script:model;QuerySucceeded=($script:mode -ne 'query-fail');Reason='fixture ownership/query refusal';Process=$null}
}
function Stop-ConfirmedOllama {
 param($Port)
 $script:modelCalls++;$script:ports+=@($Port)
 if($script:mode -eq 'model-fail'){throw 'fixture model refused'}
 if($script:mode -ne 'supervisor-restart'){$script:model=$false}
 'fixture stop processed'
}
function Get-LocalAiStatus {param($Root) [pscustomobject]@{FrpcCount=$script:frpc;OllamaOnline=$script:model;OllamaPort=12580}}
function Start-Process {throw 'No stop test may restart or launch a service'}
function Stop-Process {throw 'No unscoped process termination allowed'}
foreach($case in @('both','model-only','tunnel-only','already-off')){
 $f=if($case -in @('both','tunnel-only')){1}else{0};$m=$case -in @('both','model-only')
 Reset-StopCase $case $f $m
 $result=Invoke-LocalAiAction -Root 'fixture-only' -Action StopAll
 Check ($result.Success -and -not $script:model -and $script:frpc -eq 0) ($case+' leaves both requested components stopped')
 Check ($script:tunnelCalls -eq 1 -and $script:modelCalls -eq [int]$m) ($case+' attempts only the current model listener once')
 Check (@($script:ports|Where-Object {$_ -ne 12580}).Count -eq 0) ($case+' uses the configured model port throughout')
}
foreach($case in @('tunnel-fail','model-fail','unknown-model','query-fail','supervisor-restart','new-tunnel')){
 Reset-StopCase $case
 $result=Invoke-LocalAiAction -Root 'fixture-only' -Action StopAll
 Check (-not $result.Success -and $result.Message.Contains('停止未全部完成')) ($case+' reports partial failure instead of claiming all stopped')
 Check ($script:modelCalls -le 1 -and $script:tunnelCalls -eq 1) ($case+' does not chase replacement processes or retry blindly')
 if($case -in @('unknown-model','query-fail')){Check ($script:modelCalls -eq 0) ($case+' never terminates an unconfirmed model')}
 if($case -eq 'tunnel-fail'){Check (-not $script:model -and $script:frpc -eq 1) 'tunnel failure still attempts the explicitly requested model stop'}
 if($case -eq 'model-fail'){Check ($script:model -and $script:frpc -eq 0) 'model failure preserves actual tunnel stop result'}
}
Reset-StopCase 'invalid-port'
$result=Invoke-LocalAiAction -Root 'fixture-only' -Action StopAll
Check (-not $result.Success -and $script:tunnelCalls -eq 0 -and $script:modelCalls -eq 0) 'invalid preferences fail before any component stop'
$checks|ForEach-Object {'PASS '+$_}
'STOP_ALL_CHECKS_PASSED='+$checks.Count
'SCOPE=Isolated process/status adapters only; no real model, tunnel, Tailscale or production configuration actions.'
