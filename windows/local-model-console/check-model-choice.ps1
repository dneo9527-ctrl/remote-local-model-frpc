param([Parameter(Mandatory=$true)][string]$Executable)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.Web.Extensions
$assembly=[Reflection.Assembly]::LoadFrom([IO.Path]::GetFullPath($Executable))
$type=$assembly.GetType('LocalAIControl.ModelChoice',$true)
$checks=[Collections.Generic.List[string]]::new()
function Check([bool]$Value,[string]$Name){if(-not $Value){throw ('FAILED: '+$Name)};$checks.Add($Name)}
function Refused([scriptblock]$Action){try{[void](& $Action);return $false}catch{return $true}}
function Current([byte[]]$Bytes){$argsForCall=[object[]]::new(1);$argsForCall[0]=$Bytes;return $type.GetMethod('Current').Invoke($null,$argsForCall)}
function Save-Choice([string]$Path,[byte[]]$Expected,[string]$Name,[string[]]$Installed){[void]$type.GetMethod('Save').Invoke($null,[object[]]@($Path,$Expected,$Name,$Installed))}
$utf8=[Text.UTF8Encoding]::new($false)
$root=Join-Path ([IO.Path]::GetTempPath()) ('localai-model-choice-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($root)
$path=Join-Path $root 'model-settings.json'
$source='{ "defaultModel": "qwen3:8b", "profiles": [{"name":"long","contextTokens":16384,"maxOutputTokens":4096}], "activeProfile":"long", "provider":{"name":"keep"}, "unknown":[1,true,"保留"] }'
$original=$utf8.GetBytes($source)
[IO.File]::WriteAllBytes($path,$original)
Check ((Current $original) -ceq 'qwen3:8b') 'current default is read without rewriting configuration'
Save-Choice $path $original 'llama:small' @('qwen3:8b','llama:small')
$after=[IO.File]::ReadAllBytes($path);$saved=[IO.File]::ReadAllText($path)|ConvertFrom-Json
Check ($saved.defaultModel -ceq 'llama:small') 'selection changes the saved project default'
Check ($saved.profiles[0].contextTokens -eq 16384 -and $saved.profiles[0].maxOutputTokens -eq 4096 -and $saved.activeProfile -eq 'long') 'context profiles and active profile remain unchanged'
Check ($saved.provider.name -eq 'keep' -and $saved.unknown[0] -eq 1 -and $saved.unknown[1] -eq $true -and $saved.unknown[2] -ceq '保留') 'provider and unknown nested fields remain unchanged'
Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path+'.previous')) -ceq [Convert]::ToBase64String($original)) 'recovery backup preserves the original exact bytes'
Check ($after[0] -ne 239) 'updated JSON is UTF8 without BOM'
Check ((Refused {Save-Choice $path $original 'qwen3:8b' @('qwen3:8b')})) 'stale file snapshot cannot overwrite a newer selection'
Check ([IO.File]::ReadAllText($path).Contains('llama:small')) 'snapshot conflict leaves newer default intact'
Check ((Refused {Save-Choice $path $after 'not-installed' @('qwen3:8b')})) 'missing or removed model is rejected'
Check ((Refused {Save-Choice $path $after 'LLAMA:small' @('llama:small')})) 'model identity uses the actual installed spelling'
foreach($name in @('', ' model', "bad`nname",'has space')){Check ((Refused {Save-Choice $path $after $name @($name)})) 'invalid model identifier is rejected'}
$backup=[IO.File]::ReadAllBytes($path+'.previous')
Save-Choice $path $after 'llama:small' @('llama:small')
Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path+'.previous')) -ceq [Convert]::ToBase64String($backup)) 'saving the current model does not rotate the recovery copy'
Check ((Refused {Save-Choice (Join-Path $root 'missing.json') $null 'llama:small' @('llama:small')})) 'missing project config is not silently invented'
foreach($bad in @('{bad-json','[]','null')){Check ((Refused {Save-Choice $path $utf8.GetBytes($bad) 'llama:small' @('llama:small')})) 'malformed non-object settings are refused'}
Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) -ceq [Convert]::ToBase64String($after)) 'all refusal cases preserve existing file bytes'
$dict=[Collections.Generic.Dictionary[string,object]]::new();$dict['Models']=@('zeta:latest','alpha:8b','zeta:latest','',42,'two names')
$list=@($type.GetMethod('Available').Invoke($null,[object[]]@($dict)))
Check ($list.Count -eq 2 -and $list[0] -ceq 'alpha:8b' -and $list[1] -ceq 'zeta:latest') 'catalog filters invalid entries and deduplicates actual model names'
$resolve=$type.GetMethod('Resolve')
Check ($resolve.Invoke($null,[object[]]@('namespace/model',[string[]]@('namespace/model:latest'))) -ceq 'namespace/model:latest') 'a saved default without tag selects its exact installed latest tag'
Check ($null -eq $resolve.Invoke($null,[object[]]@('MODEL',[string[]]@('model:latest')))) 'tag convenience does not guess case-sensitive model identity'
Check ($null -eq $resolve.Invoke($null,[object[]]@('model:small',[string[]]@('model:small:latest')))) 'explicit missing tags are not rewritten to a different model'
$dict['Models']='not-an-array'
Check (@($type.GetMethod('Available').Invoke($null,[object[]]@($dict))).Count -eq 0) 'catalog does not interpret a string as individual model entries'
Check (@(Get-ChildItem -LiteralPath $root -File).Count -eq 2) 'selection creates only updated settings and its backup'
$checks | ForEach-Object {'PASS '+$_}
'MODEL_CHOICE_CHECKS_PASSED='+$checks.Count
'SCOPE=Isolated JSON and model-list fixtures; no service start, download, generation or production configuration.'
