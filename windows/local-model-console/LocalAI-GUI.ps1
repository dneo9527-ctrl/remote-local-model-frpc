#requires -Version 5.1
[CmdletBinding()]
param([string]$ProjectRoot, [switch]$LibraryOnly, [ValidateSet('','Status','Start','Stop','Cherry')][string]$BackendAction = '', [string]$ServerAddress)

# Windows PowerShell 5.1 / WPF. Keep this file UTF-8 WITH BOM for Chinese text.
# Services use the existing start-services.ps1; closing this window keeps them running.
$ErrorActionPreference = 'Stop'

function Resolve-LocalAiRoot {
    param([string]$Preferred, [string]$ScriptFolder)
    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    else {
        $candidates += $ScriptFolder
        $candidates += (Split-Path -Parent $ScriptFolder)
        $documents = [Environment]::GetFolderPath('MyDocuments')
        if ($documents) { $candidates += (Join-Path $documents 'LocalProjects\projects\qwen3-remote') }
        $candidates += (Join-Path $env:USERPROFILE 'Documents\LocalProjects\projects\qwen3-remote')
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath (Join-Path $candidate 'config\frpc.toml')) -and
            (Test-Path -LiteralPath (Join-Path $candidate 'scripts\start-services.ps1'))) {
            return [IO.Path]::GetFullPath($candidate).TrimEnd('\')
        }
    }
    throw '找不到 qwen3-remote 项目。请将面板放在项目根目录或 scripts 目录，或使用 -ProjectRoot 指定项目目录。'
}

function Read-FrpcAddress {
    param([string]$Path)
    $bytes = [IO.File]::ReadAllBytes($Path)
    $encoding = [Text.UTF8Encoding]::new($false, $true)
    $text = $encoding.GetString($bytes)
    # Only the top-level key, before the first TOML table. Never touch comments or proxy keys.
    $table = [regex]::Match($text, '(?m)^[\t ]*\[')
    $top = $text
    if ($table.Success) { $top = $text.Substring(0, $table.Index) }
    $pattern = '(?m)^(?:\uFEFF)?[\t ]*serverAddr[\t ]*=[\t ]*(["''])(?<address>[^"''\r\n]*)\1[\t ]*(?:#[^\r\n]*)?\r?$'
    $matchesFound = [regex]::Matches($top, $pattern)
    if ($matchesFound.Count -ne 1) { throw 'frpc.toml 顶层必须且只能有一条带引号的 serverAddr；配置未修改。' }
    $part = $matchesFound[0].Groups['address']
    [pscustomobject]@{ Address = $part.Value; Index = $part.Index; Length = $part.Length; Text = $text; Bytes = $bytes }
}

function Invoke-LocalProcess {
    param([string]$Executable, [string]$Arguments, [string]$WorkingDirectory, [int]$TimeoutMs = 120000)
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Executable; $info.Arguments = $Arguments
    $info.WorkingDirectory = $WorkingDirectory; $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $info
    $helperId = $null; $helperStarted = $null
    try {
        [void]$process.Start()
        $helperId = $process.Id; $helperStarted = $process.StartTime
        $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMs)) {
            # Only this helper is stopped; shared Ollama/frpc processes are not killed on timeout.
            try { $process.Kill(); [void]$process.WaitForExit(4000) } catch {}
            throw '操作等待超时。已有服务可能仍在运行，请刷新状态后再操作。'
        }
        $process.WaitForExit()
        [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $stdout.Result; Error = $stderr.Result;
            ProcessId = $helperId; StartedAt = $helperStarted; EndedAt = (Get-Date) }
    } catch {
        if ($helperId) {
            $_.Exception.Data['LocalAiHelperId'] = $helperId
            $_.Exception.Data['LocalAiHelperStartedAt'] = $helperStarted
            $_.Exception.Data['LocalAiHelperEndedAt'] = Get-Date
            $helperExited = $false; try { $helperExited = $process.HasExited } catch {}
            $_.Exception.Data['LocalAiHelperExited'] = $helperExited
        }
        throw
    } finally { $process.Dispose() }
}

function Test-ConfigBytes {
    param([string]$Path, [byte[]]$Expected)
    return [Convert]::ToBase64String([IO.File]::ReadAllBytes($Path)) -ceq [Convert]::ToBase64String($Expected)
}

function Set-ServerAddress {
    param([string]$Path, [ValidatePattern('^[A-Za-z0-9.:-]+$')][string]$ServerAddress,
        [string]$FrpcExecutable, [switch]$ValidateOnly, [switch]$PassThru, [byte[]]$ExpectedBytes)
    $before = Read-FrpcAddress $Path
    if ($PSBoundParameters.ContainsKey('ExpectedBytes') -and
        [Convert]::ToBase64String($before.Bytes) -cne [Convert]::ToBase64String($ExpectedBytes)) {
        throw '配置已被其他程序修改，本次未覆盖；请刷新后重试。'
    }
    if ($before.Address -eq $ServerAddress) {
        if ($PassThru) { return [pscustomobject]@{Changed=$false; BeforeBytes=$before.Bytes; AfterBytes=$before.Bytes} }
        return $false
    }
    $updated = $before.Text.Remove($before.Index, $before.Length).Insert($before.Index, $ServerAddress)
    $updatedBytes = [Text.UTF8Encoding]::new($false, $true).GetBytes($updated)
    $directory = Split-Path -Parent $Path
    $temporary = Join-Path $directory ('.frpc-route-' + [guid]::NewGuid().ToString('N') + '.toml')
    try {
        # Encoding, BOM, whitespace, line endings and every other setting are preserved byte-for-byte.
        [IO.File]::WriteAllBytes($temporary, $updatedBytes)
        if ($FrpcExecutable) {
            $verification = Invoke-LocalProcess $FrpcExecutable ('verify -c "' + $temporary + '"') $directory 10000
            if ($verification.ExitCode -ne 0) { throw '新线路配置未通过 frpc 校验，原配置未修改。' }
        }
        if ($ValidateOnly) { return $true }
        $now = [IO.File]::ReadAllBytes($Path)
        if ([Convert]::ToBase64String($now) -cne [Convert]::ToBase64String($before.Bytes)) {
            throw '配置已被其他程序修改，本次未覆盖；请刷新后重试。'
        }
        # Same-directory atomic replacement, retaining one local rollback copy (not exported).
        [IO.File]::Replace($temporary, $Path, ($Path + '.gui-previous.bak'))
        if ($PassThru) { return [pscustomobject]@{Changed=$true; BeforeBytes=$before.Bytes; AfterBytes=$updatedBytes} }
        return $true
    } finally { if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) } }
}

function Test-OwnedFrpc {
    param($Process, [string]$Executable, [string]$Config)
    if (-not $Process.ExecutablePath -or -not [string]::Equals($Process.ExecutablePath, $Executable, [StringComparison]::OrdinalIgnoreCase)) { return $false }
    $argument = [regex]::Match([string]$Process.CommandLine, '(?i)(?:^|\s)-c\s+(?:"(?<path>[^"]+)"|(?<path>\S+))')
    if (-not $argument.Success) { return $false }
    $path = $argument.Groups['path'].Value
    # A relative path has no provable working directory in CIM; do not claim its ownership.
    if (-not [IO.Path]::IsPathRooted($path)) { return $false }
    return [string]::Equals([IO.Path]::GetFullPath($path), [IO.Path]::GetFullPath($Config), [StringComparison]::OrdinalIgnoreCase)
}

function Get-OwnedFrpc {
    param([string]$Root)
    $exe = Join-Path $Root 'bin\frp\bridge.exe'; $config = Join-Path $Root 'config\frpc.toml'
    @(Get-CimInstance Win32_Process -Filter "Name = 'bridge.exe'" -ErrorAction Stop |
        Where-Object { Test-OwnedFrpc $_ $exe $config })
}

function Stop-OwnedFrpc {
    param([string]$Root, [array]$Targets)
    $count = 0
    if (-not $PSBoundParameters.ContainsKey('Targets')) { $Targets = @(Get-OwnedFrpc $Root) }
    foreach ($target in $Targets) {
        $current = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $target.ProcessId) -ErrorAction Stop
        if (-not $current) { continue }
        if ($current.CreationDate -ne $target.CreationDate -or -not (Test-OwnedFrpc $current (Join-Path $Root 'bin\frp\bridge.exe') (Join-Path $Root 'config\frpc.toml'))) {
            throw '隧道进程归属已变化，未停止该进程。'
        }
        $process = [Diagnostics.Process]::GetProcessById([int]$target.ProcessId)
        try { $process.Kill(); if (-not $process.WaitForExit(4000)) { throw '隧道尚未退出，请稍后刷新。' } }
        finally { $process.Dispose() }
        $count++
    }
    return $count
}

function Get-LocalOllama {
    $response = $null; $reader = $null
    try {
        $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:11434/api/tags')
        $request.Proxy = $null; $request.Timeout = 2200; $request.ReadWriteTimeout = 2200
        $response = $request.GetResponse(); $reader = [IO.StreamReader]::new($response.GetResponseStream())
        return ($reader.ReadToEnd() | ConvertFrom-Json)
    } catch { return $null }
    finally { if ($reader) { $reader.Dispose() }; if ($response) { $response.Dispose() } }
}

function Get-TunnelStatus {
    param([string]$Log, [array]$Processes)
    if ($Processes.Count -eq 0) { return [pscustomobject]@{ State = 'stopped'; Text = '未启动'; Detail = '点击“一键启动”连接所选 VPS' } }
    if ($Processes.Count -gt 1) { return [pscustomobject]@{ State = 'warning'; Text = '多个隧道进程'; Detail = '请停止隧道后重新启动' } }
    $detail = 'PID ' + $Processes[0].ProcessId
    if (Test-Path -LiteralPath $Log) {
        $started = ([datetime]$Processes[0].CreationDate).AddSeconds(-1)
        $stem = [IO.Path]::GetFileNameWithoutExtension($Log)
        $pattern = '^' + [regex]::Escape($stem) + '(?:\.log|\.\d{8}-\d{6}\.log)$'
        # frpc rotates at midnight. Search this process's recent dated files as well.
        $files = @(Get-ChildItem -LiteralPath (Split-Path -Parent $Log) -Filter ($stem + '*.log') -File |
            Where-Object { $_.Name -match $pattern } | Sort-Object LastWriteTime -Descending | Select-Object -First 4)
        foreach ($file in $files) {
        $lines = @(Get-Content -LiteralPath $file.FullName -Tail 180 -Encoding UTF8 -ErrorAction SilentlyContinue)
        [array]::Reverse($lines)
        foreach ($line in $lines) {
            $stamp = [regex]::Match($line, '^\[?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
            if (-not $stamp.Success) { continue }
            $at = [datetime]::MinValue
            if (-not [datetime]::TryParseExact($stamp.Groups[1].Value, 'yyyy-MM-dd HH:mm:ss', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$at)) { continue }
            if ($at -lt $started) { continue } # Never reuse an old process's success message.
            if ($line -match '\[ollama\].*start proxy success') { return [pscustomobject]@{ State = 'ready'; Text = '隧道已注册'; Detail = $detail + ' · ollama' } }
            if ($line -match 'start error|login to the server failed|connect to server error|connection closed|reconnect to server|try to connect to server') {
                return [pscustomobject]@{ State = 'warning'; Text = '正在连接 / 重连'; Detail = $detail + ' · 可打开日志查看原因' }
            }
            if ($line -match 'login to server success') { return [pscustomobject]@{ State = 'connecting'; Text = '等待隧道注册'; Detail = $detail } }
        }
        }
    }
    [pscustomobject]@{ State = 'connecting'; Text = '进程运行中'; Detail = $detail + ' · 等待本轮注册记录' }
}

function Get-LocalAiStatus {
    param([string]$Root)
    $config = Read-FrpcAddress (Join-Path $Root 'config\frpc.toml')
    $model = 'qwen3:8b'
    try { $settings = Get-Content -LiteralPath (Join-Path $Root 'config\model-settings.json') -Raw -Encoding UTF8 | ConvertFrom-Json; if ($settings.defaultModel) { $model = $settings.defaultModel } } catch {}
    $tags = Get-LocalOllama; $online = $null -ne $tags
    $installed = $online -and @($tags.models | Where-Object { $_.name -eq $model }).Count -gt 0
    $processes = @(Get-OwnedFrpc $Root)
    $tunnel = Get-TunnelStatus (Join-Path $Root 'logs\frpc.log') $processes
    $ts = $null; $tsExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path -LiteralPath $tsExe) {
        try { $check = Invoke-LocalProcess $tsExe 'status --json' (Split-Path -Parent $tsExe) 2500; if ($check.ExitCode -eq 0) { $ts = $check.Output | ConvertFrom-Json } } catch {}
    }
    $tsConnected = $ts -and $ts.BackendState -eq 'Running'
    $tsIp = ''; if ($tsConnected) { $tsIp = @($ts.TailscaleIPs | Where-Object { $_ -notmatch ':' }) | Select-Object -First 1 }
    [pscustomobject]@{ CheckedAt = (Get-Date); Server = $config.Address; Model = $model; ModelInstalled = $installed;
        OllamaOnline = $online; Tunnel = $tunnel; FrpcCount = $processes.Count; TailscaleConnected = [bool]$tsConnected; TailscaleIP = $tsIp }
}

function Find-CherryStudio {
    param([string]$Root)
    $candidates = @((Join-Path $env:LOCALAPPDATA 'Programs\Cherry Studio\Cherry Studio.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\CherryStudio\Cherry Studio.exe'),
        (Join-Path $env:ProgramFiles 'Cherry Studio\Cherry Studio.exe'))
    if (${env:ProgramFiles(x86)}) { $candidates += Join-Path ${env:ProgramFiles(x86)} 'Cherry Studio\Cherry Studio.exe' }
    $portable = Join-Path (Split-Path -Parent (Split-Path -Parent $Root)) 'tools\cherry-studio'
    if (Test-Path -LiteralPath $portable) {
        $candidates += @(Get-ChildItem -LiteralPath $portable -Filter 'Cherry-Studio-*-win-x64-portable.exe' -File | Sort-Object LastWriteTime -Descending | Select-Object -ExpandProperty FullName)
        $candidates += Join-Path $portable 'Cherry Studio.exe'
    }
    foreach ($file in $candidates) { if (Test-Path -LiteralPath $file -PathType Leaf) { return $file } }
    return $null
}

function Invoke-ProjectStart {
    param([string]$Root)
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $script = Join-Path $Root 'scripts\start-services.ps1'
    $command = '$ProgressPreference=''SilentlyContinue''; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $OutputEncoding=[Console]::OutputEncoding; & ''' + $script.Replace("'", "''") + ''' -NoChat -NoPause; exit $LASTEXITCODE'
    $runtime = Join-Path $Root 'state\panel-runtime'; [void][IO.Directory]::CreateDirectory($runtime)
    $wrapper = Join-Path $runtime 'Start-Services-UTF8.ps1'
    [IO.File]::WriteAllText($wrapper, $command, [Text.UTF8Encoding]::new($true))
    $result = Invoke-LocalProcess $powershell ('-NoProfile -ExecutionPolicy Bypass -File "' + $wrapper + '"') $Root 120000
    $logWarning = ''
    try {
        $logFolder = Join-Path $Root 'logs'; [void][IO.Directory]::CreateDirectory($logFolder)
        [IO.File]::WriteAllText((Join-Path $logFolder 'gui-start.log'), ($result.Output + "`r`n" + $result.Error), [Text.UTF8Encoding]::new($false))
    } catch { $logWarning = '启动日志未能保存：' + $_.Exception.Message }
    [pscustomobject]@{ Result=$result; LogWarning=$logWarning }
}

function Test-FrpcSnapshot {
    param($Process, [array]$Snapshots)
    foreach ($item in $Snapshots) {
        if ($item.ProcessId -eq $Process.ProcessId -and $item.CreationDate -eq $Process.CreationDate) { return $true }
    }
    return $false
}

function Wait-FrpcSnapshotsGone {
    param([string]$Root, [array]$Snapshots)
    if ($Snapshots.Count -eq 0) { return }
    for ($attempt=0; $attempt -lt 8; $attempt++) {
        $remaining = @(Get-OwnedFrpc $Root | Where-Object { Test-FrpcSnapshot $_ $Snapshots })
        if ($remaining.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    }
    throw '隧道停止后仍有旧进程记录，未继续切线；请刷新状态。'
}

function Start-PreviousFrpc {
    param([string]$Root)
    $exe = Join-Path $Root 'bin\frp\bridge.exe'; $config = Join-Path $Root 'config\frpc.toml'
    $process = Start-Process -FilePath $exe -ArgumentList @('-c', ('"' + $config + '"')) -WorkingDirectory (Split-Path -Parent $exe) -WindowStyle Hidden -PassThru
    try {
        for ($attempt=0; $attempt -lt 6; $attempt++) {
            if (@(Get-OwnedFrpc $Root | Where-Object { $_.ProcessId -eq $process.Id }).Count -gt 0) { return }
            Start-Sleep -Milliseconds 250
        }
        throw '原线路配置已恢复，但未确认恢复的隧道进程；请刷新状态。'
    } finally { $process.Dispose() }
}

function Restore-FrpcRoute {
    param([string]$Root, $Transaction, [array]$PreviousProcesses, $StartAttempt, $StartError)
    $config = Join-Path $Root 'config\frpc.toml'
    if (-not (Test-ConfigBytes $config $Transaction.AfterBytes)) { throw '配置已被其他程序修改，未覆盖配置或停止新进程；请刷新后核对。' }
    $helperId = $null; $helperStarted = $null; $helperEnded = $null
    if ($StartAttempt -and $StartAttempt.Result) {
        $helperId=$StartAttempt.Result.ProcessId; $helperStarted=$StartAttempt.Result.StartedAt; $helperEnded=$StartAttempt.Result.EndedAt
    } elseif ($StartError) {
        if ($StartError.Exception.Data.Contains('LocalAiHelperId') -and $StartError.Exception.Data['LocalAiHelperExited'] -ne $true) {
            throw '未确认启动辅助进程已退出，未自动停止新隧道或回滚配置；请刷新后核对。'
        }
        $helperId=$StartError.Exception.Data['LocalAiHelperId']; $helperStarted=$StartError.Exception.Data['LocalAiHelperStartedAt']; $helperEnded=$StartError.Exception.Data['LocalAiHelperEndedAt']
    }
    $newProcesses = @()
    foreach ($item in @(Get-OwnedFrpc $Root)) {
        if (Test-FrpcSnapshot $item $PreviousProcesses) { continue }
        # Only a child born during this exact helper's lifetime can be cleaned up automatically.
        if (-not $helperId -or -not $helperStarted -or -not $helperEnded -or
            $item.ParentProcessId -ne $helperId -or $item.CreationDate -lt ([datetime]$helperStarted).AddSeconds(-1) -or
            $item.CreationDate -gt ([datetime]$helperEnded).AddSeconds(1)) {
            throw '发现无法归于本次启动的新隧道，未停止该进程或回滚配置；请刷新后核对。'
        }
        $newProcesses += $item
    }
    $temporary = Join-Path (Split-Path -Parent $config) ('.frpc-route-restore-' + [guid]::NewGuid().ToString('N') + '.toml')
    try {
        [IO.File]::WriteAllBytes($temporary, $Transaction.BeforeBytes)
        $verification = Invoke-LocalProcess (Join-Path $Root 'bin\frp\bridge.exe') ('verify -c "' + $temporary + '"') (Split-Path -Parent $config) 10000
        if ($verification.ExitCode -ne 0) { throw '原配置未通过 frpc 校验，未继续恢复或重新启动。' }
        if (-not (Test-ConfigBytes $config $Transaction.AfterBytes)) { throw '恢复前配置已被其他程序修改，本次未覆盖。' }
        $unexpected = @(Get-OwnedFrpc $Root | Where-Object { -not (Test-FrpcSnapshot $_ $PreviousProcesses) -and -not (Test-FrpcSnapshot $_ $newProcesses) })
        if ($unexpected.Count -gt 0) { throw '校验期间出现新的隧道进程，未继续清理或回滚配置；请刷新后核对。' }
        if ($newProcesses.Count -gt 0) {
            [void](Stop-OwnedFrpc $Root -Targets $newProcesses)
            Wait-FrpcSnapshotsGone $Root $newProcesses
        }
        if (-not (Test-ConfigBytes $config $Transaction.AfterBytes)) { throw '恢复前配置已被其他程序修改，本次未覆盖。' }
        if (@(Get-OwnedFrpc $Root | Where-Object { -not (Test-FrpcSnapshot $_ $PreviousProcesses) }).Count -gt 0) {
            throw '恢复前出现新的隧道进程，未回滚配置；请刷新后核对。'
        }
        [IO.File]::Replace($temporary, $config, ($config + '.gui-failed-switch.bak'))
    } finally { if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) } }
    if (-not (Test-ConfigBytes $config $Transaction.BeforeBytes)) { throw '原配置恢复后又被修改，未重新启动隧道；请刷新后核对。' }
    $remaining = @(Get-OwnedFrpc $Root)
    if (@($remaining | Where-Object { -not (Test-FrpcSnapshot $_ $PreviousProcesses) }).Count -gt 0) {
        throw '原配置已恢复，但出现新的隧道进程，未另外启动；请刷新后核对。'
    }
    if ($PreviousProcesses.Count -gt 0 -and $remaining.Count -eq 0) { Start-PreviousFrpc $Root }
    if ($PreviousProcesses.Count -gt 0) { return '原配置及原线路隧道已恢复，正在重新核对注册状态。' }
    return '原配置已恢复；操作前未运行隧道，因此未额外启动。'
}

function Invoke-LocalAiAction {
    param([string]$Root, [ValidateSet('Status','Start','Stop','Cherry')][string]$Action, [string]$Server)
    $success = $true; $message = ''; $operationLock = $null; $locked = $false
    try {
        if ($Action -in @('Start','Stop')) {
            $operationLock = [Threading.Mutex]::new($false, ('Local\LocalAIControl-' + $env:USERNAME))
            try { $locked = $operationLock.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
            if (-not $locked) { throw '另一窗口正在操作服务，请稍后刷新。' }
        }
        switch ($Action) {
            'Start' {
                if (-not $Server -or $Server.Length -gt 253 -or $Server -notmatch '^[A-Za-z0-9.:-]+$' -or [Uri]::CheckHostName($Server) -eq 'Unknown') { throw '服务器地址需为有效 IP 或域名，不含协议、端口或路径。' }
                $config = Join-Path $Root 'config\frpc.toml'; $exe = Join-Path $Root 'bin\frp\bridge.exe'
                $current = Read-FrpcAddress $config; $transaction = $null; $previousProcesses = @(); $startAttempt = $null
                if ($current.Address -ne $Server) {
                    $previousProcesses = @(Get-OwnedFrpc $Root)
                    # Commit before stopping the old tunnel. A failed replacement leaves it untouched.
                    $transaction = Set-ServerAddress $config $Server $exe -PassThru -ExpectedBytes $current.Bytes
                }
                try {
                    if ($transaction -and $transaction.Changed) {
                        [void](Stop-OwnedFrpc $Root -Targets $previousProcesses)
                        Wait-FrpcSnapshotsGone $Root $previousProcesses
                    }
                    $startAttempt = Invoke-ProjectStart $Root
                    if ($startAttempt.Result.ExitCode -ne 0) { throw ('启动脚本返回错误（' + $startAttempt.Result.ExitCode + '）。请打开日志目录查看 gui-start.log。') }
                } catch {
                    $startError = $_; $detail = $_.Exception.Message
                    if ($transaction -and $transaction.Changed) {
                        try { $detail += ' ' + (Restore-FrpcRoute $Root $transaction $previousProcesses $startAttempt $startError) }
                        catch { $detail += ' 自动恢复未完成：' + $_.Exception.Message }
                    }
                    throw $detail
                }
                $message = '启动指令已完成，正在核对 Ollama 与隧道注册状态。'
                if ($startAttempt.LogWarning) { $message += ' ' + $startAttempt.LogWarning }
            }
            'Stop' { $count = Stop-OwnedFrpc $Root; $message = '已停止本项目隧道（' + $count + ' 个进程），Ollama 和 Tailscale 保持运行。' }
            'Cherry' {
                $cherry = Find-CherryStudio $Root
                if (-not $cherry) { throw '未找到 Cherry Studio。支持安装版，以及 LocalProjects\tools\cherry-studio 下的便携版。' }
                $existing = @(Get-Process -Name 'Cherry Studio' -ErrorAction SilentlyContinue)
                $activated = $false
                if ($existing.Count -gt 0) { try { $shell = New-Object -ComObject WScript.Shell; foreach ($item in $existing) { if ($shell.AppActivate($item.Id)) { $activated = $true; break } } } catch {} }
                if (-not $activated) { Start-Process -FilePath $cherry -WorkingDirectory (Split-Path -Parent $cherry) -WindowStyle Normal | Out-Null }
                $message = '已打开 Cherry Studio。'
            }
        }
    } catch { $success = $false; $message = $_.Exception.Message }
    finally { if ($locked) { $operationLock.ReleaseMutex() }; if ($operationLock) { $operationLock.Dispose() } }
    $status = $null
    try { $status = Get-LocalAiStatus $Root } catch {
        $success = $false; $statusError = '状态检查失败：' + $_.Exception.Message
        if ($message) { $message += ' ' + $statusError } else { $message = $statusError }
    }
    [pscustomobject]@{ Success = $success; Message = $message; Status = $status; Action = $Action }
}

if ($LibraryOnly) { return }

if ($BackendAction) {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    try {
        $root = Resolve-LocalAiRoot $ProjectRoot $PSScriptRoot
        $result = Invoke-LocalAiAction -Root $root -Action $BackendAction -Server $ServerAddress
        $result | ConvertTo-Json -Depth 8 -Compress
        if ($result.Success) { exit 0 } else { exit 1 }
    } catch {
        [pscustomobject]@{Success=$false;Message=$_.Exception.Message;Status=$null;Action=$BackendAction} | ConvertTo-Json -Compress
        exit 1
    }
}

# Compatibility entry: the actual user interface is the compiled WPF application.
try {
    $root = Resolve-LocalAiRoot $ProjectRoot $PSScriptRoot
    $candidates = @((Join-Path $PSScriptRoot 'LocalAI-Control.exe'), (Join-Path $root 'tools\control-panel\LocalAI-Control.exe'), (Join-Path $root 'LocalAI-Control.exe'))
    $found = $false
    foreach ($exe in $candidates) {
        if (Test-Path -LiteralPath $exe) { Start-Process -FilePath $exe -ArgumentList ('--project-root "' + $root + '"') -WindowStyle Normal | Out-Null; $found=$true; break }
    }
    if (-not $found) { throw '请将 LocalAI-Control.exe 与本文件放在一起，或放进项目 tools\control-panel 目录。' }
} catch { Add-Type -AssemblyName PresentationFramework; [void][Windows.MessageBox]::Show($_.Exception.Message,'本地模型控制台','OK','Error') }
