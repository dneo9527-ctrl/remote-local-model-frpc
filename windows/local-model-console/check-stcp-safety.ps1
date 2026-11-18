$ErrorActionPreference='Stop'
$checks=[Collections.Generic.List[string]]::new()
function Check([bool]$Value,[string]$Name){if(-not $Value){throw ('FAILED: '+$Name)};$checks.Add($Name)}
$fixture=Join-Path $PSScriptRoot ('stcp-safety-fixtures-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($fixture)
. (Join-Path $PSScriptRoot 'scripts\start-services.ps1') -LibraryOnly -ProjectRoot $fixture
$ollamaRoot=$fixture;$ollamaExe=Join-Path $fixture 'fixture-ollama.exe'
[IO.File]::WriteAllText($ollamaExe,'fixture only; never executed')
$script:spawned=@();$script:listenerReads=0;$script:tagReads=0;$script:mode='healthy'
$script:fixtureTags=[pscustomobject]@{models=@([pscustomobject]@{name='fixture-model'})}
function Get-OllamaTags {
 $script:tagReads++
 if($script:mode -eq 'healthy' -or ($script:mode -eq 'stopped' -and $script:spawned.Count -gt 0)){return $script:fixtureTags}
 return $null
}
function Get-NetTCPConnection {
 param($LocalPort,$State,$ErrorAction)
 $script:listenerReads++
 $script:lastListenerPort=$LocalPort
 if($script:mode -eq 'healthy'){throw 'Healthy Ollama must not inspect/reconfigure listeners'}
 if($script:mode -eq 'occupied'){return [pscustomobject]@{OwningProcess=123;LocalAddress='127.0.0.1'}}
 return @()
}
function Start-Process {
 param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle)
 $script:spawned+=@([pscustomobject]@{Path=$FilePath;Arguments=$ArgumentList;Host=$env:OLLAMA_HOST})
}
function Stop-Process {throw 'Fixture refuses termination of any shared process'}
$originalHost=[Environment]::GetEnvironmentVariable('OLLAMA_HOST','Process')
try {
 [Environment]::SetEnvironmentVariable('OLLAMA_HOST','fixture-parent-host:5555','Process')
 $result=Ensure-LocalOllama
 Check ($result -eq $script:fixtureTags -and $script:spawned.Count -eq 0 -and $script:listenerReads -eq 0) 'healthy shared Ollama is reused without listener changes'
 Check ($env:OLLAMA_HOST -eq 'fixture-parent-host:5555') 'reuse preserves caller environment'
 $script:mode='occupied';$failed=$false
 try {Ensure-LocalOllama}catch{$failed=$true}
 Check ($failed -and $script:spawned.Count -eq 0) 'unhealthy existing listener is not killed or replaced'
 $script:mode='stopped';$result=Ensure-LocalOllama
 Check ($result -eq $script:fixtureTags -and $script:spawned.Count -eq 1 -and $script:spawned[0].Host -eq '127.0.0.1:11434') 'new Ollama starts on loopback only'
 Check ($script:spawned[0].Path -eq $ollamaExe -and $script:spawned[0].Arguments -eq 'serve') 'only expected Ollama serve is spawned'
 Check ($env:OLLAMA_HOST -eq 'fixture-parent-host:5555') 'temporary loopback environment is restored'
 [void][IO.Directory]::CreateDirectory((Join-Path $fixture 'config'))
 $preferencePath=Join-Path $fixture 'config\panel-preferences.json'
 [IO.File]::WriteAllText($preferencePath,'{"ollamaPort":12580,"allowExternalMetadata":false}',[Text.UTF8Encoding]::new($false))
 $script:spawned=@();$script:mode='stopped';$result=Ensure-LocalOllama
 Check ($result -eq $script:fixtureTags -and $script:spawned.Count -eq 1 -and $script:spawned[0].Host -eq '127.0.0.1:12580') 'configured model port starts a loopback-only server'
 Check ($script:lastListenerPort -eq 12580) 'custom model startup inspects only the selected port, never replaces the old one'
 Check ($env:OLLAMA_HOST -eq 'fixture-parent-host:5555') 'custom port startup restores caller environment'
 [IO.File]::WriteAllText($preferencePath,'{"ollamaPort":65536}',[Text.UTF8Encoding]::new($false))
 $script:spawned=@();$failed=$false;try{[void](Ensure-LocalOllama)}catch{$failed=$true}
 Check ($failed -and $script:spawned.Count -eq 0) 'invalid configured model port cannot start a process'
}finally{[Environment]::SetEnvironmentVariable('OLLAMA_HOST',$originalHost,'Process')}
$exe=Join-Path $fixture 'bridge.exe';$config=Join-Path $fixture 'config with space\frpc.toml'
$process=[pscustomobject]@{ExecutablePath=$exe;CommandLine=('"'+$exe+'" -c "'+$config+'"')}
Check (Test-ManagedFrpc $process $exe $config) 'helper accepts exact executable and absolute config'
$process.CommandLine='bridge.exe -c frpc.toml'
Check (-not (Test-ManagedFrpc $process $exe $config)) 'helper rejects unproven relative config'
$process.CommandLine='bridge.exe -c "'+$config+'.bak"'
Check (-not (Test-ManagedFrpc $process $exe $config)) 'helper rejects config prefix collision'
$process.CommandLine='bridge.exe -c "'+$config+'" -c "'+$config+'.bak"'
Check (-not (Test-ManagedFrpc $process $exe $config)) 'helper rejects multiple config arguments'
$process.CommandLine='bridge.exe -c "'+$config+'"';$process.ExecutablePath=$exe+'.other'
Check (-not (Test-ManagedFrpc $process $exe $config)) 'helper rejects another executable'
. (Join-Path $PSScriptRoot 'LocalAI-GUI.ps1') -LibraryOnly
$script:capturedCommand=''
function Invoke-LocalProcess {
 param($Executable,$Arguments,$WorkingDirectory,$TimeoutMs)
 $wrapper=Join-Path $WorkingDirectory 'state\panel-runtime\Start-Services-UTF8.ps1'
 $script:capturedCommand=[IO.File]::ReadAllText($wrapper)
 [pscustomobject]@{ExitCode=0;Output='fixture only';Error=''}
}
$start=Invoke-ProjectStart $fixture
Check ($start.Result.ExitCode -eq 0 -and $script:capturedCommand.Contains($script:LocalAiStartScript) -and $script:capturedCommand.Contains('-ProjectRoot')) 'backend invokes this release helper with explicit project root'
Check (-not $script:capturedCommand.Contains((Join-Path $fixture 'scripts\start-services.ps1'))) 'backend never falls back to the legacy project helper'
$script:LocalAiStartScript=Join-Path $fixture 'missing-safe-helper.ps1';$failed=$false
try {Invoke-ProjectStart $fixture}catch{$failed=$true}
Check $failed 'missing release helper fails instead of running legacy helper'
# Exercise the actual child-script guard using only an isolated fixture. The
# pending marker must fail before missing model settings or any OS service call.
$guardRoot=Join-Path $fixture 'pending-guard'
[void][IO.Directory]::CreateDirectory((Join-Path $guardRoot 'config'))
[IO.File]::WriteAllText((Join-Path $guardRoot 'config\.panel-port-change.pending.json'),'{"fixture":"pending"}',[Text.UTF8Encoding]::new($false))
$guardOutput=@(& (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -File (Join-Path $PSScriptRoot 'scripts\start-services.ps1') -ProjectRoot $guardRoot -NoChat -NoPause 2>&1)
Check ($LASTEXITCODE -eq 1 -and ($guardOutput -join '').Contains('端口保存尚未完成')) 'actual STCP helper exits on pending port-save marker before reading missing settings or starting anything'
$checks|ForEach-Object{'PASS '+$_}
'STCP_SAFETY_CHECKS_PASSED='+$checks.Count
