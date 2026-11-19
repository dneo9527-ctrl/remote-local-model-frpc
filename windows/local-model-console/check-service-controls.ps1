$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot 'LocalAI-GUI.ps1') -LibraryOnly
$checks = [Collections.Generic.List[string]]::new()
function Check([bool]$Value, [string]$Name) { if (-not $Value) { throw ('FAILED: ' + $Name) }; $checks.Add($Name) }

# Every OS query and process handle is replaced. This script never stops a real service.
$script:expectedExe = Join-Path $PSScriptRoot 'fixture-install\ollama.exe'
$script:born = [datetime]::new(2026,1,2,3,4,5,[DateTimeKind]::Local)
function New-Owner([int]$Id=71, [datetime]$At=$script:born) {
    [pscustomobject]@{ ProcessId=$Id; CreationDate=$At; ExecutablePath=$script:expectedExe; CommandLine=('"' + $script:expectedExe + '" serve') }
}
function Reset-Case([string]$Name) {
    $script:mode=$Name; $script:owner=New-Owner; $script:cimReads=0; $script:killCount=0; $script:disposeCount=0
    $script:opened=@(); $script:listeners=@([pscustomobject]@{LocalPort=11434; OwningProcess=71})
}
function Get-OllamaExecutablePaths { @($script:expectedExe) }
function Get-NetTCPConnection { param($State,$ErrorAction) if ($script:mode -eq 'query-fail') { throw 'fixture access denied' }; $script:listeners }
function Get-CimInstance {
    param($ClassName,$Filter,$ErrorAction)
    $script:cimReads++
    if ($script:mode -eq 'pid-changed' -and $script:cimReads -ge 2) { return New-Owner 72 }
    if ($script:mode -eq 'creation-changed' -and $script:cimReads -ge 2) { return New-Owner 71 ($script:born.AddMinutes(1)) }
    if ($script:mode -eq 'path-changed' -and $script:cimReads -ge 2) { $changed=New-Owner; $changed.ExecutablePath+='-other'; return $changed }
    $script:owner
}
function Open-OllamaProcessHandle {
    param([int]$ProcessId)
    $script:opened += $ProcessId
    $started = if ($script:mode -eq 'handle-reused') { $script:born.AddMinutes(1) } else { $script:born }
    $fake = [pscustomobject]@{ StartTime=$started; HasExited=($script:mode -eq 'already-exited') }
    $fake | Add-Member ScriptMethod Kill {
        $script:killCount++
        if ($script:mode -eq 'restarted') { $script:owner=New-Owner 99 ($script:born.AddMinutes(2)); $script:listeners=@([pscustomobject]@{LocalPort=11434;OwningProcess=99}) }
        else { $script:listeners=@() }
    }
    $fake | Add-Member ScriptMethod WaitForExit { param($Milliseconds) return $script:mode -ne 'exit-timeout' }
    $fake | Add-Member ScriptMethod Dispose { $script:disposeCount++ }
    return $fake
}
function Stop-Process { throw 'Fixture refuses any unscoped process termination' }
function Stop-OwnedFrpc { throw 'Ollama control must never stop frpc' }
function Start-Process { throw 'Ollama stop must never start another program' }
function Expect-Refused([string]$Name) { $refused=$false; try { [void](Stop-ConfirmedOllama) } catch { $refused=$true }; Check ($refused -and $script:killCount -eq 0) $Name }

Reset-Case 'valid'
$snapshot=Get-OllamaStopSnapshot
Check ($snapshot.CanStop -and $snapshot.Process.ProcessId -eq 71) 'known serve listener can be stopped explicitly'
$script:owner.ExecutablePath += '.other'
Check (-not (Get-OllamaStopSnapshot).CanStop) 'matching process name with another executable is rejected'
Reset-Case 'valid'; $script:owner.CommandLine=('"'+$script:expectedExe+'" run serve')
Check (-not (Get-OllamaStopSnapshot).CanStop) 'an Ollama CLI argument named serve is not a server'
Reset-Case 'valid'; $script:owner.CreationDate=$null
Check (-not (Get-OllamaStopSnapshot).CanStop) 'missing creation identity is rejected'
Reset-Case 'valid'; $script:listeners=@()
Check (-not (Get-OllamaStopSnapshot).CanStop) 'no selected-port listener gives no stop target'
Reset-Case 'valid'; $script:listeners+=@([pscustomobject]@{LocalPort=11434;OwningProcess=72})
Check (-not (Get-OllamaStopSnapshot).CanStop) 'ambiguous listener ownership is rejected'
Reset-Case 'valid'; $script:listeners+=@([pscustomobject]@{LocalPort=11434;OwningProcess=71})
Check ((Get-OllamaStopSnapshot).CanStop) 'IPv4 and IPv6 records for one owner are not ambiguous'
Reset-Case 'query-fail'
Check (-not (Get-OllamaStopSnapshot).CanStop) 'unreadable ownership becomes disabled action, not guessed ownership'
foreach ($mode in @('pid-changed','creation-changed','path-changed','handle-reused')) { Reset-Case $mode; Expect-Refused ($mode+' is revalidated without killing any process') }
Reset-Case 'valid'; $message=Stop-ConfirmedOllama
Check ($script:killCount -eq 1 -and $script:opened.Count -eq 1 -and $script:opened[0] -eq 71 -and $script:disposeCount -eq 1) 'only validated process handle is terminated and disposed'
Check ($message.Contains('frp') -and $message.Contains('Tailscale')) 'stop result describes services retained'
Reset-Case 'already-exited'; $message=Stop-ConfirmedOllama
Check ($script:killCount -eq 0 -and $script:disposeCount -eq 1) 'already-exited target never causes another kill'
Reset-Case 'restarted'; $message=Stop-ConfirmedOllama
Check ($script:killCount -eq 1 -and $script:opened.Count -eq 1 -and $message.Contains('未停止新进程')) 'supervisor restart is reported without chasing a new PID'
Reset-Case 'exit-timeout'; $failed=$false; try { [void](Stop-ConfirmedOllama) } catch { $failed=$true }
Check ($failed -and $script:killCount -eq 1 -and $script:disposeCount -eq 1) 'exit timeout is reported and handle disposed without repeated termination'

$fixture=Join-Path ([IO.Path]::GetTempPath()) ('localai-service-control-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory((Join-Path $fixture 'config'))
$preferences=Join-Path $fixture 'config\panel-preferences.json'
Check ((Get-LocalOllamaPort $fixture) -eq 11434) 'missing port preference keeps the established default'
[IO.File]::WriteAllText($preferences,'{"allowExternalMetadata":false}',[Text.UTF8Encoding]::new($false))
Check ((Get-LocalOllamaPort $fixture) -eq 11434) 'legacy metadata preference remains compatible'
[IO.File]::WriteAllText($preferences,'{"ollamaPort":12580,"allowExternalMetadata":false}',[Text.UTF8Encoding]::new($false))
Check ((Get-LocalOllamaPort $fixture) -eq 12580) 'configured custom model port is loaded'
foreach ($value in @('0','65536','"invalid"','null','1.5','true')) {
    [IO.File]::WriteAllText($preferences,('{"ollamaPort":'+$value+'}'),[Text.UTF8Encoding]::new($false))
    $failed=$false; try { [void](Get-LocalOllamaPort $fixture) } catch { $failed=$true }
    Check $failed ('invalid configured port '+$value+' is refused')
}
[IO.File]::WriteAllText($preferences,'"invalid root"',[Text.UTF8Encoding]::new($false))
$failed=$false; try { [void](Get-LocalOllamaPort $fixture) } catch { $failed=$true }
Check $failed 'non-object preference root is refused'
[IO.File]::WriteAllText($preferences,'[{"ollamaPort":12580}]',[Text.UTF8Encoding]::new($false))
$failed=$false; try { [void](Get-LocalOllamaPort $fixture) } catch { $failed=$true }
Check $failed 'single-element JSON array cannot masquerade as a preference object'
Reset-Case 'valid'; $script:listeners=@([pscustomobject]@{LocalPort=12580;OwningProcess=71},[pscustomobject]@{LocalPort=11434;OwningProcess=66})
Check ((Get-OllamaStopSnapshot -Port 12580).Process.ProcessId -eq 71) 'custom-port ownership ignores unrelated default-port listener'
$message=Stop-ConfirmedOllama -Port 12580
Check ($script:killCount -eq 1 -and $script:opened[0] -eq 71) 'custom-port stop targets only the selected listener'

# Status/dispatch contract is isolated from HTTP, Tailscale and frpc processes too.
function Read-FrpcAddress { param($Path) [pscustomobject]@{Address='192.0.2.1'} }
function Get-LocalOllama { param($Port) $script:statusPort=$Port; [pscustomobject]@{models=@()} }
function Get-OwnedFrpc { param($Root) @() }
function Get-TunnelStatus { param($Log,$Processes) [pscustomobject]@{State='stopped';Text='fixture';Detail=''} }
function Invoke-LocalProcess { throw 'Fixture does not invoke Tailscale or helpers' }
[IO.File]::WriteAllText($preferences,'{"ollamaPort":12580}',[Text.UTF8Encoding]::new($false))
Reset-Case 'valid';$script:listeners=@([pscustomobject]@{LocalPort=12580;OwningProcess=71})
$status=Get-LocalAiStatus $fixture
Check ($status.OllamaPort -eq 12580 -and $script:statusPort -eq 12580 -and $status.OllamaCanStop -and $status.OllamaProcessId -eq 71) 'status forwards configured model port and safe stop capability'
$result=Invoke-LocalAiAction -Root $fixture -Action StopOllama
Check ($result.Success -and $result.Action -eq 'StopOllama' -and $script:killCount -eq 1 -and -not $result.Status.OllamaCanStop) 'explicit action dispatch stops selected model API and refreshes status without frpc'
[IO.File]::WriteAllText((Join-Path $fixture 'config\.panel-port-change.pending.json'),'{"fixture":"pending"}',[Text.UTF8Encoding]::new($false))
$script:killCount=0
$result=Invoke-LocalAiAction -Root $fixture -Action Start -Server '192.0.2.1'
Check (-not $result.Success -and $result.Message.Contains('端口保存尚未完成') -and $script:killCount -eq 0) 'pending port-save marker blocks backend start before any service or configuration action'
$checks | ForEach-Object { 'PASS '+$_ }
'SERVICE_CONTROL_CHECKS_PASSED='+$checks.Count
