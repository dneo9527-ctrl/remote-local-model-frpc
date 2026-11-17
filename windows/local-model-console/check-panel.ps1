param([string]$FrpcExecutable, [string]$LiveProjectRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'LocalAI-GUI.ps1') -LibraryOnly
$root = Join-Path $PSScriptRoot 'fixtures'
[void][IO.Directory]::CreateDirectory($root)
if ($FrpcExecutable) {
 $FrpcExecutable = (Resolve-Path -LiteralPath $FrpcExecutable -ErrorAction Stop).Path
 if (-not (Test-Path -LiteralPath $FrpcExecutable -PathType Leaf)) { throw 'FrpcExecutable must be a file.' }
}
$exe = if ($FrpcExecutable) { $FrpcExecutable } else { Join-Path $root 'fixture-frpc.exe' }
$checks = [Collections.Generic.List[string]]::new()
function Check([bool]$Value, [string]$Name) { if (-not $Value) { throw ('FAILED: ' + $Name) }; $checks.Add($Name) }

foreach ($bom in @($false,$true)) {
 foreach ($eol in @("`n","`r`n")) {
  foreach ($quote in @('"',"'")) {
   $path = Join-Path $root ([guid]::NewGuid().ToString('N') + '.toml')
   $text = @('# serverAddr = "untouched-comment"', ('  serverAddr  = ' + $quote + '192.0.2.10' + $quote + '   # keep comment'),
     'serverPort = 7000', 'auth.token = "test-token-DO-NOT-CHANGE"', '[[proxies]]','name = "ollama"','type = "stcp"',
     'secretKey = "test-secret-DO-NOT-CHANGE"','localIP = "127.0.0.1"','localPort = 11434') -join $eol
   [IO.File]::WriteAllText($path,$text,[Text.UTF8Encoding]::new($bom))
   $before = [IO.File]::ReadAllBytes($path)
   $verifyExe = if ($bom -or -not $FrpcExecutable) { '' } else { $FrpcExecutable }; [void](Set-ServerAddress $path '198.51.100.20' $verifyExe)
   $expected = [Text.UTF8Encoding]::new($false).GetBytes(([Text.UTF8Encoding]::new($false).GetString($before)).Replace(($quote+'192.0.2.10'+$quote),($quote+'198.51.100.20'+$quote)))
   Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -ceq [Convert]::ToBase64String($expected)) ('only serverAddr bytes changed: bom='+$bom+' eol='+$eol.Length+' quote='+$quote)
   Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path+'.gui-previous.bak')) -ceq [Convert]::ToBase64String($before)) 'backup preserves original exact bytes'
   $hash = (Get-FileHash -LiteralPath $path).Hash
   $verifyExe = if ($bom -or -not $FrpcExecutable) { '' } else { $FrpcExecutable }; [void](Set-ServerAddress $path '198.51.100.20' $verifyExe)
   Check ((Get-FileHash -LiteralPath $path).Hash -ceq $hash) 'same route is a no-op'
   [void](Set-ServerAddress $path '203.0.113.30' $verifyExe)
   Check ((Read-FrpcAddress $path).Address -eq '203.0.113.30') 'third route supported'
  }
 }
}
$invalidCases = @('serverPort = 7000', "serverAddr = 'one'`nserverAddr = 'two'")
# A real frpc parser is required to reject semantic TOML type errors.
if ($FrpcExecutable) { $invalidCases += "serverAddr = '192.0.2.10'`nserverPort = 'invalid-integer'" } else { Write-Output 'SKIP frpc semantic parser check: provide -FrpcExecutable to enable' }
foreach ($content in $invalidCases) {
 $path = Join-Path $root ([guid]::NewGuid().ToString('N')+'.toml')
 [IO.File]::WriteAllText($path,$content,[Text.UTF8Encoding]::new($false))
 $before = (Get-FileHash -LiteralPath $path).Hash; $refused = $false
 try { [void](Set-ServerAddress $path '203.0.113.30' $FrpcExecutable) } catch { $refused = $true }
 Check ($refused -and (Get-FileHash -LiteralPath $path).Hash -ceq $before) 'ambiguous/missing/invalid TOML refused without mutation'
}
$path = Join-Path $root 'nested.toml'
[IO.File]::WriteAllText($path,"serverAddr = '192.0.2.10'`n[custom]`nserverAddr = 'nested-must-stay'",[Text.UTF8Encoding]::new($false))
[void](Set-ServerAddress $path '203.0.113.30')
Check (([IO.File]::ReadAllText($path)).Contains("serverAddr = 'nested-must-stay'")) 'nested serverAddr untouched'
$project = Join-Path $root 'Project with space'
[void][IO.Directory]::CreateDirectory((Join-Path $project 'config')); [void][IO.Directory]::CreateDirectory((Join-Path $project 'scripts'))
[IO.File]::WriteAllText((Join-Path $project 'config\frpc.toml'),'serverAddr = "192.0.2.10"')
[IO.File]::WriteAllText((Join-Path $project 'scripts\start-services.ps1'),'# fixture only')
Check ((Resolve-LocalAiRoot '' (Join-Path $project 'scripts')) -eq $project) 'project/scripts layout resolves'
Check ((Resolve-LocalAiRoot '' $project) -eq $project) 'project root layout resolves'
Check ((Resolve-LocalAiRoot $project $root) -eq $project) 'explicit ProjectRoot resolves'
$config = Join-Path $project 'config\frpc.toml'
$owned = [pscustomobject]@{ ExecutablePath=$exe; CommandLine=('"'+$exe+'" -c "'+$config+'"') }
Check (Test-OwnedFrpc $owned $exe $config) 'exact owned executable and config accepted'
Check (-not (Test-OwnedFrpc $owned ($exe+'-other') $config)) 'unrelated executable excluded'
$owned.CommandLine = 'bridge.exe -c "'+$config+'.bak"'
Check (-not (Test-OwnedFrpc $owned $exe $config)) 'config prefix collision excluded'
$owned.CommandLine = 'bridge.exe -c frpc.toml'
Check (-not (Test-OwnedFrpc $owned $exe $config)) 'unproven relative config excluded'
$owned.CommandLine = 'bridge.exe -c "'+$config+'" -c "'+$config+'.bak"'
Check (-not (Test-OwnedFrpc $owned $exe $config)) 'multiple config arguments excluded'
$log = Join-Path $root 'frpc.log'; $now=Get-Date
$fake=@([pscustomobject]@{ProcessId=123;CreationDate=$now})
[IO.File]::WriteAllText($log,(($now.AddMinutes(-10).ToString('yyyy-MM-dd HH:mm:ss'))+' [I] [ollama] start proxy success'))
Check ((Get-TunnelStatus $log $fake).State -ne 'ready') 'old log cannot claim current tunnel ready'
[IO.File]::WriteAllText($log,($now.ToString('yyyy-MM-dd HH:mm:ss')+' [I] [ollama] start proxy success'))
Check ((Get-TunnelStatus $log $fake).State -eq 'ready') 'current process registration recognized'
[IO.File]::AppendAllText($log,("`n"+$now.AddSeconds(1).ToString('yyyy-MM-dd HH:mm:ss')+' [W] connection closed'))
Check ((Get-TunnelStatus $log $fake).State -eq 'warning') 'newer connection failure overrides success'
Check ((Get-TunnelStatus $log @()).State -eq 'stopped') 'stopped process cannot claim ready from log'
$rotated=Join-Path $root ('frpc.'+$now.ToString('yyyyMMdd')+'-000000.log')
[IO.File]::WriteAllText($rotated,($now.ToString('yyyy-MM-dd HH:mm:ss')+' [I] [ollama] start proxy success'))
[IO.File]::WriteAllText($log,'')
Check ((Get-TunnelStatus $log $fake).State -eq 'ready') 'midnight rotation retains current process registration'
[IO.File]::WriteAllText($log,($now.AddSeconds(1).ToString('yyyy-MM-dd HH:mm:ss')+' [W] connection closed'))
Check ((Get-TunnelStatus $log $fake).State -eq 'warning') 'new daily log failure overrides rotated success'
[IO.File]::Delete($rotated)
if ($LiveProjectRoot) {
 $real = (Resolve-Path -LiteralPath $LiveProjectRoot -ErrorAction Stop).Path
 $status = Get-LocalAiStatus $real
 Check ([bool]$status.Model) 'explicit live project status readable'
} else { Write-Output 'SKIP live project check: no -LiveProjectRoot provided' }
Check ((Get-ChildItem -LiteralPath $root -Filter '.frpc-route-*' -File).Count -eq 0) 'temporary configs cleaned'
$checks | ForEach-Object { 'PASS '+$_ }
'CHECKS_PASSED='+$checks.Count
