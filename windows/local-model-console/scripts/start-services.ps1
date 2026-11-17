[CmdletBinding()]
param([switch]$NoChat, [switch]$NoPause, [switch]$StatusOnly, [switch]$LibraryOnly, [string]$ProjectRoot)

$ErrorActionPreference = 'Stop'
$ollamaRoot = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
$projectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$frpRoot = Join-Path $projectRoot 'bin\frp'
$ollamaExe = Join-Path $ollamaRoot 'ollama.exe'
$frpcExe = Join-Path $frpRoot 'bridge.exe'
$frpcConfig = Join-Path $projectRoot 'config\frpc.toml'
$frpcLog = Join-Path $projectRoot 'logs\frpc.log'
$tailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
$modelName = ''
$mutex = $null
$ownsMutex = $false
$exitCode = 0
# This helper is STCP-only. It does not reconfigure shared Ollama, Tailscale or firewall rules.

function Get-OllamaTags {
    $response = $null; $reader = $null
    try {
        $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:11434/api/tags')
        $request.Proxy = $null; $request.Timeout = 3000; $request.ReadWriteTimeout = 3000
        $response = $request.GetResponse(); $reader = [IO.StreamReader]::new($response.GetResponseStream())
        return ($reader.ReadToEnd() | ConvertFrom-Json)
    } catch { return $null }
    finally { if ($reader) { $reader.Dispose() }; if ($response) { $response.Dispose() } }
}

function Get-TailscaleState {
    if (Test-Path -LiteralPath $tailscaleExe) {
        try {
            $json = & $tailscaleExe status --json 2>$null
            if ($LASTEXITCODE -eq 0) { return ($json | ConvertFrom-Json) }
        } catch {}
    }
    return $null
}

function Test-ManagedFrpc {
    param($Process, [string]$Executable, [string]$Config)
    if (-not $Process.ExecutablePath -or -not [string]::Equals($Process.ExecutablePath, $Executable, [StringComparison]::OrdinalIgnoreCase)) { return $false }
    $arguments = [regex]::Matches([string]$Process.CommandLine, '(?i)(?:^|\s)-c\s+(?:"(?<path>[^"]+)"|(?<path>\S+))(?=\s|$)')
    if ($arguments.Count -ne 1) { return $false }
    $path = $arguments[0].Groups['path'].Value
    # CIM provides no proven cwd for relative config paths. Never adopt one.
    if (-not [IO.Path]::IsPathRooted($path)) { return $false }
    try { return [string]::Equals([IO.Path]::GetFullPath($path), [IO.Path]::GetFullPath($Config), [StringComparison]::OrdinalIgnoreCase) }
    catch { return $false }
}

function Get-ManagedFrpc {
    @(Get-CimInstance Win32_Process -Filter "Name = 'bridge.exe'" -ErrorAction Stop |
        Where-Object { Test-ManagedFrpc $_ $frpcExe $frpcConfig })
}

function Ensure-LocalOllama {
    $tags = Get-OllamaTags
    if ($tags) { return $tags } # Reuse any already healthy shared Ollama without restarting it.
    $listeners = @(Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) { throw '11434 已有监听但 Ollama API 不可用；请检查现有服务，本次未停止或重配它。' }
    if (-not (Test-Path -LiteralPath $ollamaExe -PathType Leaf)) { throw '未找到 Ollama，请先安装或启动已有 Ollama。' }
    $previous = [Environment]::GetEnvironmentVariable('OLLAMA_HOST', 'Process')
    try {
        $env:OLLAMA_HOST = '127.0.0.1:11434'
        Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WorkingDirectory $ollamaRoot -WindowStyle Hidden | Out-Null
    } finally { [Environment]::SetEnvironmentVariable('OLLAMA_HOST', $previous, 'Process') }
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $tags = Get-OllamaTags
        if ($tags) { return $tags }
        Start-Sleep -Milliseconds 500
    }
    throw 'Ollama API 启动超时，请检查现有服务；本次不会终止共享进程。'
}

function Get-FrpcConnectionState {
    if (-not (Test-Path -LiteralPath $frpcLog)) { return '尚无日志' }
    $lines = @(Get-Content -LiteralPath $frpcLog -Tail 150 -ErrorAction SilentlyContinue)
    [array]::Reverse($lines)
    foreach ($line in $lines) {
        if ($line -match '\[ollama\] start proxy success') { return '已注册 ollama 隧道' }
        if ($line -match 'start error|login to the server failed|connect to server error|reconnect to server|connection closed') {
            return '连接异常，后台将继续重连（查看 frpc.log）'
        }
        if ($line -match 'login to server success') { return '已登录，等待隧道注册' }
    }
    return '正在连接（查看 frpc.log）'
}

function Show-Status {
    $ts = Get-TailscaleState
    $tags = Get-OllamaTags
    $frpc = @(Get-ManagedFrpc)
    Write-Host ''
    Write-Host '========== 本地模型 STCP ==========' -ForegroundColor Cyan
    if ($tags) {
        $modelExists = @($tags.models | Where-Object { $_.name -eq $modelName }).Count -gt 0
        Write-Host ('Ollama       : 在线；默认模型 ' + $modelName + ' 已安装 = ' + $modelExists) -ForegroundColor Green
    } else { Write-Host 'Ollama       : API 不可用' -ForegroundColor Yellow }
    if ($ts -and $ts.BackendState -eq 'Running') {
        $tsIp = @($ts.TailscaleIPs | Where-Object { $_ -notmatch ':' }) | Select-Object -First 1
        Write-Host ('Tailscale    : 已连接 ' + $tsIp + '（辅助状态；未验证 Ollama 远程监听）')
    } else { Write-Host 'Tailscale    : 未连接；frp 仍可独立工作' -ForegroundColor Yellow }
    if ($frpc.Count -gt 0) {
        Write-Host ('frp          : PID ' + ($frpc.ProcessId -join ', ') + '；' + (Get-FrpcConnectionState))
    } else { Write-Host 'frp          : 未运行' -ForegroundColor Yellow }
    Write-Host '远程客户端   : 运行该设备的 visitor；客户端地址以该设备实际配置为准。'
    Write-Host ('默认模型     : ' + $modelName)
    Write-Host ('frp 日志     : ' + $frpcLog)
    Write-Host '===================================='
    Write-Host ''
}

if ($LibraryOnly) { return }

try {
    try { $Host.UI.RawUI.WindowTitle = '本地模型 | frp STCP' } catch {}
    $modelSettings = Get-Content -LiteralPath (Join-Path $projectRoot 'config\model-settings.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $modelName = [string]$modelSettings.defaultModel
    if (-not $modelName) { throw 'config/model-settings.json 缺少 defaultModel，请选择已有模型。' }
    if ($StatusOnly) { Show-Status; return }
    $mutex = [Threading.Mutex]::new($false, ('Local\Qwen3HybridStart-' + $env:USERNAME))
    try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw '另一启动窗口正在准备服务，请稍候再查看状态。' }
    foreach ($required in @($frpcExe, $frpcConfig)) {
        if (-not (Test-Path -LiteralPath $required)) { throw ('未找到文件：' + $required) }
    }
    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'logs') -Force | Out-Null

    Write-Host '[1/2] 复用 Ollama；未运行时仅启动回环监听...' -ForegroundColor Cyan
    $tags = Ensure-LocalOllama

    Write-Host '[2/2] 检查并启动 frpc（STCP）...' -ForegroundColor Cyan
    $verifyOutput = & $frpcExe verify -c $frpcConfig 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'frpc 配置校验失败；请私下检查配置，未将配置内容写入日志。' }
    if (@(Get-ManagedFrpc).Count -eq 0) {
        Start-Process -FilePath $frpcExe -ArgumentList @('-c', ('"' + $frpcConfig + '"')) `
            -WorkingDirectory $frpRoot -WindowStyle Hidden
        Start-Sleep -Seconds 3
        if (@(Get-ManagedFrpc).Count -eq 0) { throw ('frpc 启动后退出，请查看：' + $frpcLog) }
    }
    Show-Status
    $mutex.ReleaseMutex()
    $ownsMutex = $false
    if (@($tags.models | Where-Object { $_.name -eq $modelName }).Count -eq 0) {
        Write-Host ('未发现默认模型 ' + $modelName + '；请在模型客户端中选择已安装模型，或修改 config/model-settings.json。') -ForegroundColor Yellow
    } elseif (-not $NoChat) {
        Write-Host '进入本地对话；输入 /bye 退出。退出后 Ollama 与 frp 继续在后台运行。' -ForegroundColor DarkGray
        $previous = [Environment]::GetEnvironmentVariable('OLLAMA_HOST', 'Process')
        try { $env:OLLAMA_HOST = '127.0.0.1:11434'; & $ollamaExe run $modelName }
        finally { [Environment]::SetEnvironmentVariable('OLLAMA_HOST', $previous, 'Process') }
    }
} catch {
    $exitCode = 1
    Write-Host ('启动失败：' + $_.Exception.Message) -ForegroundColor Red
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
    if (-not $NoPause) { [void](Read-Host '按回车关闭窗口；后台服务继续运行') }
}
exit $exitCode
