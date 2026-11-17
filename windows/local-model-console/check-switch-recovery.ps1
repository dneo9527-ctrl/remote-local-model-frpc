$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'LocalAI-GUI.ps1') -LibraryOnly
$fixtureBase=Join-Path $PSScriptRoot ('switch-recovery-fixtures-' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($fixtureBase)
$checks=[Collections.Generic.List[string]]::new()
function Check([bool]$Value,[string]$Name){if(-not $Value){throw ('FAILED: '+$Name)};$checks.Add($Name)}
function Fake-Process([int]$Id,[int]$Parent=1){[pscustomobject]@{ProcessId=$Id;ParentProcessId=$Parent;CreationDate=(Get-Date);ExecutablePath=(Join-Path $script:caseRoot 'bin\frp\bridge.exe');CommandLine=('bridge.exe -c "'+$script:caseConfig+'"')}}
function Get-OwnedFrpc {
 param([string]$Root)
 $script:getCalls++
 if($script:mode -eq 'precommit-external' -and $script:getCalls -eq 1){[IO.File]::AppendAllText($script:caseConfig,"`r`n# external-before-commit",[Text.UTF8Encoding]::new($false))}
 $result=@($script:fakeOwned)
 if($script:ghostCount -gt 0){$result+=@($script:ghosts);$script:ghostCount--}
 $result
}
function Stop-OwnedFrpc {
 param([string]$Root,[array]$Targets)
 if(-not $PSBoundParameters.ContainsKey('Targets')){throw 'Fixture refuses unsnapshotted stop'}
 foreach($target in $Targets){
  if($script:mode -eq 'stop-fail'){throw 'SIMULATED_STOP_FAILURE'}
  if($script:mode -eq 'partial-stop' -and $target.ProcessId -eq 101){throw 'SIMULATED_PARTIAL_STOP_FAILURE'}
  $script:stoppedIds+=@($target.ProcessId)
  $script:fakeOwned=@($script:fakeOwned|Where-Object{-not (Test-FrpcSnapshot $_ @($target))})
  if($script:mode -eq 'cleanup-new-process' -and $target.ProcessId -eq 200){$script:fakeOwned+=@(Fake-Process 201 999)}
  if($script:mode -eq 'cim-delay'){$script:ghosts=@($target);$script:ghostCount=2}
 }
 return $Targets.Count
}
function Start-PreviousFrpc {
 param([string]$Root)
 if(-not (Test-ConfigBytes $script:caseConfig $script:originalBytes)){throw 'Fixture refuses recovery under wrong config'}
 $script:recoveryStarts++
 $script:fakeOwned=@(Fake-Process 300)
}
function Get-LocalAiStatus {param([string]$Root)[pscustomobject]@{FrpcCount=$script:fakeOwned.Count;Server=(Read-FrpcAddress $script:caseConfig).Address}}
function Invoke-LocalProcess {
 param([string]$Executable,[string]$Arguments,[string]$WorkingDirectory,[int]$TimeoutMs)
 if($Executable -eq (Join-Path $script:caseRoot 'bin\frp\bridge.exe') -and $Arguments -like 'verify -c *'){
  $script:verifyCalls++
  if($script:mode -eq 'verify-external' -and $script:verifyCalls -eq 1){[IO.File]::AppendAllText($script:caseConfig,"`r`n# external-during-verify",[Text.UTF8Encoding]::new($false))}
  if($script:mode -eq 'restore-verify-new-process' -and $script:verifyCalls -eq 2){$script:fakeOwned+=@(Fake-Process 201 999)}
  $exitCode=0
  if($script:mode -eq 'verify-reject' -or ($script:mode -eq 'restore-verify-reject' -and $script:verifyCalls -gt 1)){$exitCode=1}
  return [pscustomobject]@{ExitCode=$exitCode;Output='fixture verify';Error=''}
 }
 if(-not $Executable.EndsWith('\powershell.exe',[StringComparison]::OrdinalIgnoreCase)){throw 'Unexpected process attempt blocked'}
 $script:startCalls++
 $helperStart=(Get-Date).AddSeconds(-1)
 $exitCode=0
 switch($script:mode){
  'startup-fail'{$script:fakeOwned=@(Fake-Process 200 900);$exitCode=1}
  'restore-verify-new-process'{$script:fakeOwned=@(Fake-Process 200 900);$exitCode=1}
  'cleanup-new-process'{$script:fakeOwned=@(Fake-Process 200 900);$exitCode=1}
  'restore-verify-reject'{$script:fakeOwned=@(Fake-Process 200 900);$exitCode=1}
  {$_ -in @('startup-throw','helper-still-running')}{
   $script:fakeOwned=@(Fake-Process 200 900)
   $failure=[TimeoutException]::new('SIMULATED_HELPER_TIMEOUT')
   $failure.Data['LocalAiHelperId']=900;$failure.Data['LocalAiHelperStartedAt']=$helperStart;$failure.Data['LocalAiHelperEndedAt']=Get-Date
   $failure.Data['LocalAiHelperExited']=($script:mode -ne 'helper-still-running')
   throw $failure
  }
  'unknown-new'{$script:fakeOwned=@(Fake-Process 200 999);$exitCode=1}
  'missing-helper-metadata'{$script:fakeOwned=@(Fake-Process 200 900);throw [TimeoutException]::new('SIMULATED_UNATTRIBUTED_TIMEOUT')}
  'startup-external'{[IO.File]::AppendAllText($script:caseConfig,"`r`n# external-during-start",[Text.UTF8Encoding]::new($false));$exitCode=1}
  'invalid-external-start'{[IO.File]::WriteAllText($script:caseConfig,'invalid external fixture');$exitCode=42}
  'restore-lock'{$script:restoreLock=[IO.File]::Open($script:caseConfig,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);$exitCode=1}
  default{if($script:fakeOwned.Count -eq 0){$script:fakeOwned=@(Fake-Process 200 900)}}
 }
 [pscustomobject]@{ExitCode=$exitCode;Output='fixture startup';Error='';ProcessId=900;StartedAt=$helperStart;EndedAt=(Get-Date)}
}
function New-Case([string]$Name,[bool]$Running=$true){
 $script:mode=$Name;$script:caseRoot=Join-Path $fixtureBase $Name;$script:caseConfig=Join-Path $script:caseRoot 'config\frpc.toml'
 [void][IO.Directory]::CreateDirectory((Split-Path -Parent $script:caseConfig))
 $content="# keep comment`r`nserverAddr = '192.0.2.10' # preserve spacing`r`nserverPort = 7000`r`nauth.token = 'fixture-token-only'`r`n[[proxies]]`r`nname = 'ollama'`r`ntype = 'stcp'`r`nsecretKey = 'fixture-secret-only'`r`nlocalPort = 11434`r`n"
 [IO.File]::WriteAllText($script:caseConfig,$content,[Text.UTF8Encoding]::new($false))
 $script:originalBytes=[IO.File]::ReadAllBytes($script:caseConfig)
 $script:fakeOwned=@();if($Running){$script:fakeOwned=@(Fake-Process 100)}
 $script:stoppedIds=@();$script:recoveryStarts=0;$script:startCalls=0;$script:getCalls=0;$script:verifyCalls=0;$script:restoreLock=$null;$script:ghostCount=0;$script:ghosts=@()
}
function Run-Start{Invoke-LocalAiAction -Root $script:caseRoot -Action Start -Server '198.51.100.20'}
New-Case 'commit-lock'
$lock=[IO.File]::Open($script:caseConfig,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try{$result=Run-Start}finally{$lock.Dispose()}
Check (-not $result.Success -and $script:stoppedIds.Count -eq 0 -and $script:fakeOwned.Count -eq 1 -and (Test-ConfigBytes $script:caseConfig $script:originalBytes)) 'real commit file lock leaves original tunnel and bytes untouched'
New-Case 'verify-reject';$result=Run-Start
Check (-not $result.Success -and $script:stoppedIds.Count -eq 0 -and (Test-ConfigBytes $script:caseConfig $script:originalBytes)) 'verification rejection never stops old tunnel'
foreach($name in @('precommit-external','verify-external')){
 New-Case $name;$result=Run-Start
 Check (-not $result.Success -and $script:stoppedIds.Count -eq 0 -and [IO.File]::ReadAllText($script:caseConfig).Contains('# external-')) ($name+' preserves external edit and old tunnel')
}
New-Case 'stop-fail';$result=Run-Start
Check (-not $result.Success -and (Test-ConfigBytes $script:caseConfig $script:originalBytes) -and $script:fakeOwned[0].ProcessId -eq 100 -and $script:recoveryStarts -eq 0) 'stop failure restores config while keeping surviving old instance'
New-Case 'partial-stop';$script:fakeOwned+=@(Fake-Process 101);$result=Run-Start
Check (-not $result.Success -and (Test-ConfigBytes $script:caseConfig $script:originalBytes) -and $script:fakeOwned.Count -eq 1 -and $script:fakeOwned[0].ProcessId -eq 101 -and $script:recoveryStarts -eq 0) 'partial old stop failure preserves survivor without duplicate restart'
foreach($name in @('startup-fail','startup-throw')){
 New-Case $name;$result=Run-Start
 Check (-not $result.Success -and (Test-ConfigBytes $script:caseConfig $script:originalBytes) -and $script:recoveryStarts -eq 1 -and $script:fakeOwned[0].ProcessId -eq 300) ($name+' restores original bytes and restarts only frpc')
 Check (($script:stoppedIds -join ',') -eq '100,200') ($name+' cleans only snapshotted old and attributed new instances')
 Check (Test-ConfigBytes ($script:caseConfig+'.gui-previous.bak') $script:originalBytes) ($name+' preserves original recovery backup')
}
foreach($name in @('unknown-new','missing-helper-metadata','helper-still-running')){
 New-Case $name;$result=Run-Start
 Check (-not $result.Success -and -not (Test-ConfigBytes $script:caseConfig $script:originalBytes) -and $script:fakeOwned[0].ProcessId -eq 200 -and ($script:stoppedIds -join ',') -eq '100' -and $script:recoveryStarts -eq 0) ($name+' refuses unsafe cleanup and rollback')
}
New-Case 'startup-external';$result=Run-Start
Check (-not $result.Success -and [IO.File]::ReadAllText($script:caseConfig).Contains('# external-during-start') -and $script:recoveryStarts -eq 0) 'external edit after commit is never overwritten by recovery'
New-Case 'restore-verify-new-process';$result=Run-Start
Check (-not $result.Success -and -not (Test-ConfigBytes $script:caseConfig $script:originalBytes) -and ($script:stoppedIds -join ',') -eq '100' -and @($script:fakeOwned|Where-Object{$_.ProcessId -eq 201}).Count -eq 1 -and $script:recoveryStarts -eq 0) 'unknown process appearing during restore verification blocks cleanup and config rollback'
New-Case 'cleanup-new-process';$result=Run-Start
Check (-not $result.Success -and -not (Test-ConfigBytes $script:caseConfig $script:originalBytes) -and ($script:stoppedIds -join ',') -eq '100,200' -and $script:fakeOwned[0].ProcessId -eq 201 -and $script:recoveryStarts -eq 0) 'unknown process appearing during cleanup blocks config rollback'
New-Case 'invalid-external-start';$result=Run-Start
Check (-not $result.Success -and $result.Message.Contains('42') -and [IO.File]::ReadAllText($script:caseConfig) -eq 'invalid external fixture' -and $script:recoveryStarts -eq 0) 'status parse failure preserves original startup error and external edit'
New-Case 'restore-lock'
try{$result=Run-Start}finally{if($script:restoreLock){$script:restoreLock.Dispose()}}
Check (-not $result.Success -and -not (Test-ConfigBytes $script:caseConfig $script:originalBytes) -and $script:recoveryStarts -eq 0) 'real atomic restore lock failure blocks old frpc restart'
Check (Test-ConfigBytes ($script:caseConfig+'.gui-previous.bak') $script:originalBytes) 'failed atomic restore keeps original recovery backup'
New-Case 'restore-verify-reject';$result=Run-Start
Check (-not $result.Success -and $script:recoveryStarts -eq 0 -and $script:fakeOwned[0].ProcessId -eq 200 -and ($script:stoppedIds -join ',') -eq '100') 'restore verification failure stops before cleanup or config replacement'
New-Case 'success';$result=Run-Start
$expected=([Text.UTF8Encoding]::new($false)).GetBytes(([Text.UTF8Encoding]::new($false).GetString($script:originalBytes)).Replace('192.0.2.10','198.51.100.20'))
Check ($result.Success -and (Test-ConfigBytes $script:caseConfig $expected) -and $script:recoveryStarts -eq 0 -and ($script:stoppedIds -join ',') -eq '100') 'successful switch changes only serverAddr and does not roll back'
New-Case 'cim-delay';$result=Run-Start
Check ($result.Success -and $script:ghostCount -eq 0 -and $script:recoveryStarts -eq 0) 'bounded CIM snapshot delay is tolerated'
New-Case 'same-line';$result=Invoke-LocalAiAction -Root $script:caseRoot -Action Start -Server '192.0.2.10'
Check ($result.Success -and (Test-ConfigBytes $script:caseConfig $script:originalBytes) -and $script:stoppedIds.Count -eq 0 -and -not [IO.File]::Exists($script:caseConfig+'.gui-previous.bak')) 'same-line start is a config and stop no-op'
New-Case 'domain-case';[IO.File]::WriteAllText($script:caseConfig,"serverAddr = 'Node.Example.com'",[Text.UTF8Encoding]::new($false));$script:originalBytes=[IO.File]::ReadAllBytes($script:caseConfig)
$result=Invoke-LocalAiAction -Root $script:caseRoot -Action Start -Server 'node.example.com'
Check ($result.Success -and (Test-ConfigBytes $script:caseConfig $script:originalBytes) -and $script:stoppedIds.Count -eq 0) 'case-equivalent domain does not restart existing tunnel'
New-Case 'log-fail';[IO.File]::WriteAllText((Join-Path $script:caseRoot 'logs'),'fixture blocks log directory');$result=Run-Start
Check ($result.Success -and (Read-FrpcAddress $script:caseConfig).Address -eq '198.51.100.20' -and $script:recoveryStarts -eq 0 -and $script:fakeOwned[0].ProcessId -eq 200) 'logging failure after successful startup never causes destructive rollback'
New-Case 'initially-stopped' $false;$result=Run-Start
Check ($result.Success -and $script:stoppedIds.Count -eq 0 -and $script:recoveryStarts -eq 0) 'switch when previously stopped starts normally'
Check ((Get-ChildItem -LiteralPath $fixtureBase -Filter '.frpc-route-*' -File -Recurse).Count -eq 0) 'all staged route and restore files cleaned'
$checks|ForEach-Object{'PASS '+$_}
'SWITCH_RECOVERY_CHECKS_PASSED='+$checks.Count
