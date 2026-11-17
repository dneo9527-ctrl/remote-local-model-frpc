[CmdletBinding()]
param([switch]$NoChat, [switch]$NoPause, [switch]$StatusOnly)

$ErrorActionPreference = 'Stop'
$ollamaRoot = Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
$projectRoot = Split-Path -Parent $PSScriptRoot
$frpRoot = Join-Path $projectRoot 'bin\frp'
$ollamaExe = Join-Path $ollamaRoot 'ollama.exe'
$frpcExe = Join-Path $frpRoot 'bridge.exe'
$frpcConfig = Join-Path $projectRoot 'config\frpc.toml'
$frpcLog = Join-Path $projectRoot 'logs\frpc.log'
$tailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
$modelSettings = Get-Content -LiteralPath (Join-Path $projectRoot 'config\model-settings.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$modelName = $modelSettings.defaultModel
$mutex = $null
$ownsMutex = $false
$exitCode = 0
try { $Host.UI.RawUI.WindowTitle = '本地模型 | Tailscale + frp' } catch {}

function Get-OllamaTags {
    try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3 } catch { $null }
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

function Get-ManagedFrpc {
    @(Get-CimInstance Win32_Process -Filter "Name = 'bridge.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -eq $frpcExe -and $_.CommandLine -and
            ($_.CommandLine.Contains($frpcConfig) -or
             $_.CommandLine -match '(?i)\s-c\s+"?(?:\.\\)?frpc\.toml"?(?:\s|$)')
        })
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
    Write-Host '========== 本地模型双线路 ==========' -ForegroundColor Cyan
    if ($tags) {
        $modelExists = @($tags.models | Where-Object { $_.name -eq $modelName }).Count -gt 0
        Write-Host ('Ollama       : 在线；默认模型 ' + $modelName + ' 已安装 = ' + $modelExists) -ForegroundColor Green
    } else { Write-Host 'Ollama       : API 不可用' -ForegroundColor Yellow }
    if ($ts -and $ts.BackendState -eq 'Running') {
        $tsIp = @($ts.TailscaleIPs | Where-Object { $_ -notmatch ':' }) | Select-Object -First 1
        Write-Host ('Tailscale    : http://' + $tsIp + ':11434') -ForegroundColor Green
        Write-Host ('OpenAI TS    : http://' + $tsIp + ':11434/v1')
    } else { Write-Host 'Tailscale    : 未连接；frp 仍可独立工作' -ForegroundColor Yellow }
    if ($frpc.Count -gt 0) {
        Write-Host ('frp          : PID ' + ($frpc.ProcessId -join ', ') + '；' + (Get-FrpcConnectionState))
    } else { Write-Host 'frp          : 未运行' -ForegroundColor Yellow }
    Write-Host 'Mac 混合入口 : http://127.0.0.1:11435 （运行 Mac 启动包后）'
    Write-Host 'OpenAI 混合  : http://127.0.0.1:11435/v1'
    Write-Host ('默认模型     : ' + $modelName)
    Write-Host ('frp 日志     : ' + $frpcLog)
    Write-Host '===================================='
    Write-Host ''
}

try {
    if ($StatusOnly) { Show-Status; return }
    $mutex = [Threading.Mutex]::new($false, ('Local\Qwen3HybridStart-' + $env:USERNAME))
    try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw '另一启动窗口正在准备服务，请稍候再查看状态。' }
    foreach ($required in @($ollamaExe, $frpcExe, $frpcConfig)) {
        if (-not (Test-Path -LiteralPath $required)) { throw ('未找到文件：' + $required) }
    }
    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'logs') -Force | Out-Null

    Write-Host '[1/3] 检查 Ollama 和远程监听...' -ForegroundColor Cyan
    $listeners = @(Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)
    $remoteListeners = @($listeners | Where-Object { $_.LocalAddress -in @('0.0.0.0','::') })
    if ($remoteListeners.Count -eq 0) {
        foreach ($ownerId in @($listeners.OwningProcess | Sort-Object -Unique)) {
            $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId"
            if ($owner.ExecutablePath -ne $ollamaExe) { throw '11434 端口被其他程序占用，未停止该程序。' }
        }
        if ($listeners.Count -gt 0) {
            Write-Host 'Ollama 当前仅监听本机，正在重启监听进程以启用远程访问。' -ForegroundColor Yellow
            $listeners.OwningProcess | Sort-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force }
            Start-Sleep -Seconds 1
        }
        $env:OLLAMA_HOST = '0.0.0.0:11434'
        Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WorkingDirectory $ollamaRoot -WindowStyle Hidden
    }
    $tags = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $tags = Get-OllamaTags
        if ($tags) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $tags) { throw 'Ollama API 启动超时，请检查 11434 端口。' }

    Write-Host '[2/3] 检查 Tailscale...' -ForegroundColor Cyan
    $ts = Get-TailscaleState
    if (-not $ts -or $ts.BackendState -ne 'Running') {
        Write-Host '请在 Tailscale 中登录并连接；本次继续启动 frp 备用线路。' -ForegroundColor Yellow
    }
    $fw = @(Get-NetFirewallRule -DisplayName 'Ollama Tailscale' -ErrorAction SilentlyContinue |
        Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' })
    if ($fw.Count -eq 0) {
        Write-Host '未发现原有 Tailscale 防火墙放行规则；frp 不需要该入站规则。' -ForegroundColor Yellow
    }

    Write-Host '[3/3] 检查并启动 frpc（STCP）...' -ForegroundColor Cyan
    $verifyOutput = & $frpcExe verify -c $frpcConfig 2>&1
    if ($LASTEXITCODE -ne 0) { throw ('frpc 配置校验失败：' + ($verifyOutput -join ' ')) }
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
        $env:OLLAMA_HOST = '127.0.0.1:11434'
        & $ollamaExe run $modelName
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
