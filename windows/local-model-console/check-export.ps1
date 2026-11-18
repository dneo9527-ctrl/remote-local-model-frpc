param([string]$FrpcExecutable)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -Path (Join-Path $PSScriptRoot 'PanelExport.cs')
$checks = [Collections.Generic.List[string]]::new()
function Check([bool]$Value, [string]$Name) {
 if (-not $Value) { throw ('FAILED: ' + $Name) }
 $checks.Add($Name)
}
function Refused([string]$Text, [string]$Name) {
 $blocked = $false
 try { [void][LocalAIControl.VisitorExport]::Build($Text, '198.51.100.20') }
 catch { $blocked = $true; Check (-not $_.Exception.Message.Contains('fixture-token') -and -not $_.Exception.Message.Contains('fixture-secret')) ('refusal does not expose credentials: '+$Name) }
 Check $blocked $Name
}
$fixture = @'
# auth.token = "misleading-comment"
serverAddr = "192.0.2.10"
serverPort = 7001
auth.method = 'token'
auth.token = "fixture-token#literal"
transport.tls.enable = true
log.to = "provider-private-log-location"

[[proxies]]
name = 'ollama'
type = "stcp"
secretKey = 'fixture-secret#literal'
localIP = "127.0.0.1"
localPort = 11434
transport.useCompression = true
'@
$original = $fixture
$output = [LocalAIControl.VisitorExport]::Build($fixture, '198.51.100.20')
Check ($fixture -ceq $original) 'input configuration is immutable'
Check ($output.Contains('serverAddr = "198.51.100.20"') -and -not $output.Contains('192.0.2.10')) 'selected route replaces only exported destination'
Check ($output.Contains('serverPort = 7001')) 'original nondefault frps port is preserved'
Check ($output.Contains('auth.token = "fixture-token#literal"')) 'quoted hash in token is preserved'
Check ($output.Contains('secretKey = "fixture-secret#literal"')) 'quoted hash in STCP secret is preserved'
Check ($output.Contains('[[visitors]]') -and -not $output.Contains('[[proxies]]')) 'export is a visitor rather than a provider copy'
Check ($output.Contains('serverName = "ollama"') -and $output.Contains('name = "local-model-visitor"')) 'visitor targets original provider proxy name'
Check ($output.Contains('bindAddr = "127.0.0.1"') -and $output.Contains('bindPort = 11436')) 'visitor binds receiving-device loopback with explicit default port'
Check (-not $output.Contains('provider-private-log-location') -and -not $output.Contains('localPort =') -and -not $output.Contains('misleading-comment')) 'provider paths, local endpoint and comments do not leak'
Check ($output.Contains('transport.tls.enable = true') -and $output.Contains('transport.useCompression = true')) 'supported transport settings are preserved'
$custom = [LocalAIControl.VisitorExport]::Build($fixture, 'model.example.org', 21436)
Check ($custom.Contains('serverAddr = "model.example.org"') -and $custom.Contains('bindPort = 21436')) 'domain route and user-chosen bind port are supported'
# Build equivalent table-form auth after common connection fields.
$table = @'
serverAddr = '192.0.2.10'
serverPort = 7001
user = 'provider-owner'
[auth]
method = 'token'
token = 'fixture-token-table'
additionalScopes = ['HeartBeats', 'NewWorkConns']
[[proxies]]
name = 'ollama'
type = 'stcp'
secretKey = 'fixture-secret-table'
localPort = 11434
'@
$tableOutput = [LocalAIControl.VisitorExport]::Build($table,'203.0.113.30')
Check ($tableOutput.Contains('auth.token = "fixture-token-table"') -and $tableOutput.Contains('auth.additionalScopes = ["HeartBeats", "NewWorkConns"]')) 'auth table and explicit scopes are supported'
Check ($tableOutput.Contains('serverUser = "provider-owner"') -and -not $tableOutput.Contains("`nuser =")) 'provider namespace maps to serverUser without changing visitor identity'
$escaped = $fixture.Replace('"fixture-token#literal"','"fixture-token\"quote\\slash\t\u4E2D\U0001F600"').Replace("'fixture-secret#literal'",'"fixture-secret\"quote\\slash\n"')
$escapedOutput = [LocalAIControl.VisitorExport]::Build($escaped,'198.51.100.20')
Check ($escapedOutput.Contains('fixture-token\"quote\\slash\t中') -and $escapedOutput.Contains('fixture-secret\"quote\\slash\n')) 'quotes, backslashes, controls and Unicode round trip safely'
Check ($escapedOutput.Contains([char]::ConvertFromUtf32(0x1f600))) 'supplementary Unicode scalar is preserved'
$defaultPort = [LocalAIControl.VisitorExport]::Build(($fixture -replace 'serverPort = 7001\r?\n',''),'198.51.100.20')
Check ($defaultPort.Contains('serverPort = 7000')) 'omitted serverPort uses documented frp default'
Refused ($fixture.Replace('auth.method = ''token''','auth.method = ''oidc''')) 'OIDC authentication is not guessed'
Refused ($fixture + "`n[auth.tokenSource]`ntype = 'file'`nfile.path = 'fixture-file'") 'external tokenSource is refused'
Refused ($fixture.Replace('fixture-token#literal','{{ .Envs.FRP_TOKEN }}')) 'Go template authentication is refused'
Refused ($fixture.Replace('fixture-token#literal','${FRP_TOKEN}')) 'environment interpolation is refused'
Refused ($fixture.Replace('"fixture-token#literal"','"""fixture-token"""')) 'TOML multiline strings are refused'
Refused ($fixture.Replace('"fixture-token#literal"',"'fixture-token'`nauth.token = 'duplicate'")) 'duplicate auth token is refused'
Refused ($fixture.Replace('secretKey = ''fixture-secret#literal''','secretKey = ''''')) 'missing shared secret is refused'
Refused ($fixture.Replace('serverPort = 7001','serverPort = 70000')) 'out-of-range frps port is refused'
Refused ($fixture.Replace('serverPort = 7001','serverPort = "7001"')) 'string port is refused'
Refused ($fixture.Replace('serverAddr = "192.0.2.10"','serverAddr = "192.0.2.10"' + "`nincludes = ['private/*.toml']")) 'external includes are refused'
Refused ($fixture.Replace('transport.tls.enable = true','transport.tls.trustedCaFile = "fixture-ca.pem"')) 'nonportable TLS certificate requirements are refused rather than dropped'
Refused ($fixture.Replace('transport.tls.enable = true','transport.proxyURL = "http://private-proxy.example"')) 'private upstream proxy is refused rather than leaked'
Refused ($fixture.Replace('transport.tls.enable = true','transport.tls.enable = "true"')) 'invalid transport boolean is refused'
Refused ($fixture.Replace('type = "stcp"','type = "tcp"')) 'non-STCP provider is refused'
Refused ($fixture + "`n[[proxies]]`nname = 'other-model'`ntype = 'stcp'`nsecretKey = 'other-fixture'`nlocalPort = 11434") 'ambiguous model proxies are refused'
Refused ($fixture + "`nenabled = false") 'disabled model proxy is refused'
Refused ($fixture + "`nallowUsers = ['special-visitor-user']") 'visitor identity restrictions require manual setup'
Refused ($fixture.Replace('"fixture-token#literal"','"fixture-token\uD800"')) 'unpaired Unicode surrogate escape is refused'
Refused ($fixture.Replace('"fixture-token#literal"','"fixture-token\q"')) 'unknown TOML string escape is refused'
Refused ($fixture + "`n[auth]`nmethod = 'token'") 'conflicting dotted and table auth is refused'
foreach ($server in @('https://model.example.org','model.example.org:7000','node name','example.org/path','$(arbitrary)')) {
 $blocked = $false
 try { [void][LocalAIControl.VisitorExport]::Build($fixture,$server) } catch { $blocked = $true }
 Check $blocked 'invalid selected destination is refused'
}
foreach ($port in @(0,-1,65536)) {
 $blocked = $false
 try { [void][LocalAIControl.VisitorExport]::Build($fixture,'198.51.100.20',$port) } catch { $blocked = $true }
 Check $blocked 'invalid visitor bind port is refused'
}
if ($FrpcExecutable) {
 $FrpcExecutable = (Resolve-Path -LiteralPath $FrpcExecutable -ErrorAction Stop).Path
 $scratch = Join-Path ([IO.Path]::GetTempPath()) ('localai-export-fixtures-'+[guid]::NewGuid().ToString('N'))
 [void][IO.Directory]::CreateDirectory($scratch)
 try {
  $index = 0
  foreach ($document in @($output,$tableOutput,$escapedOutput,$custom)) {
   $index++
   $file = Join-Path $scratch ('visitor-'+$index+'.toml')
   [IO.File]::WriteAllText($file,$document,[Text.UTF8Encoding]::new($false))
   $result = & $FrpcExecutable verify -c $file 2>&1
   Check ($LASTEXITCODE -eq 0) 'official frpc validates generated visitor fixture without connecting'
  }
 } finally {
  foreach ($file in [IO.Directory]::GetFiles($scratch)) { [IO.File]::Delete($file) }
  [IO.Directory]::Delete($scratch)
 }
} else { Write-Output 'SKIP official frpc parser check: provide -FrpcExecutable to enable' }
$checks | ForEach-Object { 'PASS '+$_ }
'EXPORT_CHECKS_PASSED='+$checks.Count
