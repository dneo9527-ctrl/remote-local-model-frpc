#requires -Version 5.1
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'PanelConfig.cs')
$checks=[Collections.Generic.List[string]]::new()
function Check([bool]$Value,[string]$Name){if(-not $Value){throw ('FAILED: '+$Name)};$checks.Add($Name)}
function Refuses([scriptblock]$Action,[string]$Name){$refused=$false;try{& $Action|Out-Null}catch{$refused=$true};Check $refused $Name}
$utf8=[Text.UTF8Encoding]::new($false,$true)
$base=@'
# serverPort = 9999 must stay a comment
serverAddr = 'node.example.invalid'
  serverPort  = 7000   # retain spacing and annotation
auth.method = "token"
auth.token = 'fixture-token-ONLY-keep-exactly'
[log]
to = 'logs/frpc.log'
[[proxies]]
name = 'other'
type = 'stcp'
secretKey = 'fixture-other-secret'
localPort = 9999
[[proxies]]
name = 'ollama'
type = 'stcp'
secretKey = "fixture-secret-ONLY-\"keep\""
localIP = '127.0.0.1'
 localPort = 11434 # keep private settings and comments
[proxies.transport]
useEncryption = true
localPort = 8888
'@
foreach($newline in @("`n","`r`n")){
 foreach($bom in @($false,$true)){
  $text=$base.Replace("`r`n","`n").Replace("`n",$newline)
  if($bom){$text=[string][char]0xfeff+$text}
  $expected=$text.Replace('serverPort  = 7000','serverPort  = 7443').Replace(' localPort = 11434',' localPort = 12434')
  $changed=[LocalAIControl.PortConfig]::Change($text,7443,12434)
  Check ($changed -ceq $expected) ('only two numeric values changed: BOM='+$bom+' newline='+$newline.Length)
  $ports=[LocalAIControl.PortConfig]::Read($changed)
  Check ($ports[0] -eq 7443 -and $ports[1] -eq 12434) 'read returns selected frps and Ollama ports'
  Check ([LocalAIControl.PortConfig]::Change($text,7000,11434) -ceq $text) 'unchanged ports preserve exact content'
 }
}
$quoted=$base.Replace("name = 'ollama'",'name = "oll\u0061ma"').Replace("type = 'stcp'",'type = "stcp"')
Check (([LocalAIControl.PortConfig]::Read($quoted))[1] -eq 11434) 'single-line TOML string escapes recognized without changing private strings'
$authTable=$base.Replace('auth.method = "token"',"[auth]`nmethod = 'token'").Replace("auth.token = 'fixture-token-ONLY-keep-exactly'","token = 'fixture-token-ONLY-keep-exactly'")
Check (([LocalAIControl.PortConfig]::Read($authTable))[0] -eq 7000) 'ordinary auth table allowed'
foreach($badPort in @(0,-1,65536)){
 Refuses { [LocalAIControl.PortConfig]::Change($base,$badPort,11434) } ('invalid frps port refused '+$badPort)
 Refuses { [LocalAIControl.PortConfig]::Change($base,7000,$badPort) } ('invalid Ollama port refused '+$badPort)
}
$cases=@{
 'missing serverPort'=$base.Replace('  serverPort  = 7000   # retain spacing and annotation','')
 'duplicate serverPort'=$base.Replace('  serverPort  = 7000','serverPort = 7443' + "`n" + 'serverPort = 7000')
 'quoted port'=$base.Replace('serverPort  = 7000',"serverPort = '7000'")
 'leading zero port'=$base.Replace('serverPort  = 7000','serverPort = 07000')
 'missing Ollama localPort'=$base.Replace(' localPort = 11434 # keep private settings and comments','')
 'duplicate Ollama localPort'=$base.Replace(' localPort = 11434',"localPort = 12434`nlocalPort = 11434")
 'duplicate Ollama proxy'=$base.Replace("name = 'other'","name = 'ollama'")
 'wrong Ollama type'=$base.Replace("name = 'ollama'`ntype = 'stcp'","name = 'ollama'`ntype = 'tcp'")
 'unsupported array value'=$base.Replace('useEncryption = true','array = [1,2]')
 'multiline string'=$base.Replace("secretKey = 'fixture-other-secret'","secretKey = '''line`nmore'''")
 'dynamic template'=$base.Replace("serverAddr = 'node.example.invalid'",'serverAddr = "{{ .Envs.FRPS }}"')
 'invalid string escape'=$base.Replace("secretKey = 'fixture-other-secret'",'secretKey = "bad\q"')
 'quoted key'=$base.Replace('serverPort  = 7000','"serverPort" = 7000')
 'unsupported array table'=$base.Replace('[[proxies]]','[[unknown]]')
}
foreach($name in $cases.Keys){$bad=$cases[$name];Refuses {[LocalAIControl.PortConfig]::Change($bad,7443,12434)} $name}
$folder=Join-Path ([IO.Path]::GetTempPath()) ('localai-settings-check-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($folder)
$path=Join-Path $folder 'frpc.toml'
$before=$utf8.GetBytes([string][char]0xfeff+$base)
[IO.File]::WriteAllBytes($path,$before)
$written=[LocalAIControl.PortConfig]::Apply($path,$before,7443,12434)
Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -ceq [Convert]::ToBase64String($written)) 'atomic save publishes expected candidate'
Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path+'.port-previous.bak')) -ceq [Convert]::ToBase64String($before)) 'private recovery copy keeps original exact bytes and BOM'
Check ($written[0] -eq 239 -and $written[1] -eq 187 -and $written[2] -eq 191) 'saved UTF8 BOM retained'
$after=[IO.File]::ReadAllBytes($path)
Refuses {[LocalAIControl.PortConfig]::Apply($path,$before,7555,12555)} 'stale original snapshot rejected'
Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -ceq [Convert]::ToBase64String($after)) 'stale snapshot never overwrites externally changed content'
$lock=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try { Refuses {[LocalAIControl.PortConfig]::Apply($path,$after,7555,12555)} 'real external read-only lock blocks atomic replacement' } finally { $lock.Dispose() }
Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -ceq [Convert]::ToBase64String($after)) 'failed replacement keeps configuration intact'
Check ((Get-ChildItem -LiteralPath $folder -Filter '*.tmp' -File).Count -eq 0) 'failed replacement cleans staged temporary file'
$backup=[IO.File]::ReadAllBytes($path+'.port-previous.bak')
[void][LocalAIControl.PortConfig]::Apply($path,$after,7443,12434)
Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path+'.port-previous.bak')) -ceq [Convert]::ToBase64String($backup)) 'no-op save leaves recovery point unchanged'
Refuses {[LocalAIControl.PortConfig]::Apply($path,$after,0,12434)} 'invalid requested port never touches file'
Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -ceq [Convert]::ToBase64String($after)) 'invalid port preserves file'
$checks|ForEach-Object{'PASS '+$_}
'SETTINGS_CHECKS_PASSED='+$checks.Count
'Scope: isolated temporary TOML only; no production settings, listeners, services, or model calls.'
