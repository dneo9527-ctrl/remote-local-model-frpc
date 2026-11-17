# 本地模型控制台 / VPS 切换面板（Windows 1.0.1）

这是 Windows 电脑当前使用的原生面板源码：**C# / WPF 界面 + PowerShell 5.1 后端**，不是 Sub2API 部署页。保留一键启动、停止本项目隧道、状态刷新、Cherry Studio 入口、线路增删改、Ping/TCP 延迟、国旗与位置、连接说明、日志入口。

Mac 端可以直接阅读和编辑这些文件、整合功能与视觉设计；WPF 程序本身只在 Windows x64 运行。Mac 整合和更新器继续由 Mac 侧维护，见 [交接说明](MAC_HANDOFF.md)。

## 源码入口

| 文件 | 用途 |
| --- | --- |
| `LocalAI-Control.cs` | 窗口逻辑、线路、测速、状态、进程间单实例 |
| `LocalAI.UI.xaml` | 完整界面布局与样式 |
| `LocalAI-GUI.ps1` | 服务/配置操作后端，及兼容启动入口 |
| `build.ps1` / `app.manifest` / `flags/` | EXE 编译与内嵌资源 |
| `scripts/start-services.ps1` | 原项目 Ollama、Tailscale、frpc 启动辅助脚本 |
| `启动本地模型.bat` | 原双击入口，优先使用 EXE |
| `examples/` | 无真实地址、无凭据的配置示例 |
| `check-*.ps1` | 配置、恢复、界面回归检查 |

源文件来自现用 1.0.1。上传版只将默认 VPS 和测试夹具地址换成文档专用示例网段，并去除测试脚本对某台电脑绝对路径的依赖。现用电脑的源码、设置、程序均未被此上传覆盖。

## 构建与打开

Windows x64，需 Windows PowerShell 5.1 与 .NET Framework 4.8；使用系统自带 .NET Framework 编译器，不依赖 Pinokio、Python、Docker 或 npm。

在本目录的 Windows PowerShell 中执行：

```powershell
& .\build.ps1
& .\LocalAI-Control.exe --project-root 'D:\YourExistingModelProject'
```

将示例路径换成真实已有项目。已有项目至少需要：

```text
YourExistingModelProject/
  config/frpc.toml                 # 自己的私有配置
  config/model-settings.json       # 自己的模型名
  scripts/start-services.ps1       # 本目录附的原辅助脚本
  bin/frp/bridge.exe               # 官方 frpc.exe 的原项目文件名
  tools/control-panel/LocalAI-Control.exe
  启动本地模型.bat
```

把编译好的 EXE 放进已有项目的 `tools/control-panel/`，BAT 放到项目根目录；双击 BAT 即可打开。**打开面板不等于点击启动，不会自动启动模型推理。** 日常使用保留自己的 `config`，不要用示例覆盖。

全新项目请自行从 [Ollama](https://ollama.com/) 和 [frp 官方发行](https://github.com/fatedier/frp/releases) 安装适合本机的依赖，再按上述结构准备配置。示例地址不可连接；填入自己有权限的 frps 地址、token 与 STCP secretKey，远端 visitor 必须匹配。frpc 需支持 `verify -c`（原项目使用 0.72.0）。

## 原行为与边界

- 切线只替换 `frpc.toml` 顶层 `serverAddr`；token、secretKey、serverPort、注释和换行保持；目标线路仍使用原有 serverPort。
- 状态区分 Ollama API、默认模型是否安装、frpc 进程、隧道注册。TCP 可连不等于 STCP 认证或端到端问答成功。
- “停止隧道”只处理能证实归属的项目 frpc；共享 Ollama/Tailscale 保留，关窗口后服务继续。
- 原 `start-services.ps1` 在点击启动后可能重启已确认归属的 Ollama 监听，并使其监听 `0.0.0.0:11434` 以供 Tailscale 访问。应先核对自己机器的网络隔离/防火墙。只用 STCP、希望保留回环监听时，需要另行调整辅助脚本；上传本身没有执行它。
- 旧辅助脚本的 `Get-ManagedFrpc` 仍保留相对 `-c frpc.toml` 兼容判断，比面板后端的严格归属规则宽。后续整合应统一归属验证，不能把旧行为宣称为新版安全保证。
- 原面板约每 6 秒创建后台 PowerShell 刷新状态，约每 30 秒测速；本次只补源码，不声称已完成低开销刷新改造。
- 国旗 PNG 是随原面板打包的国家标志资源；可见 `flags/`，在线候补地址见 C# 源码。位置是 IP 库或手动标注，不能保证真实机房。
- `127.0.0.1` 始终指当前客户端设备，Mac/手机各自需要 visitor；模型端口和管理端口不能混用。
- 旧版测速固定使用 TCP 7000；若 frps 使用其他端口，需要后续将探测端口与真实配置统一，本次保留原行为。

## 检查

在隔离目录执行以下检查，不会运行真实服务启停。UI 用例构造临时窗口但不显示正式窗口。

```powershell
& .\check-panel.ps1
& .\check-switch-recovery.ps1
powershell.exe -NoProfile -STA -File .\check-ui-fixes.ps1
```

可选真实 frpc **仅解析临时夹具**（不会启动隧道）：

```powershell
& .\check-panel.ps1 -FrpcExecutable 'D:\Tools\frpc.exe'
```

未传 frpc 时语义 TOML 类型检查明确 SKIP；其余字节保留、归属和日志夹具仍执行。真实项目只读状态检查需显式传 `-LiveProjectRoot`，默认不探测某台电脑的 Cherry Studio 或指定模型。

PS1 保存为 UTF-8 BOM，供 Windows PowerShell 5.1 正确读取中文；C# 编译明确 `/codepage:65001`。检查会在源码旁创建 `fixtures/`、`switch-recovery-fixtures-*`、`ui-regression/`，均已加入忽略。真实执行结果与平台界限见 [验证记录](VALIDATION.md)。

## 配置与发布

本目录不含真实 TOML、visitor、模型权重、账号凭据、运行状态、历史日志或 EXE。`examples` 中所有密码均为显式占位符，不会自动生成可运行私人配置。不要把 `config/`、`state/`、`.gui-*.bak`、`.previous` 或日志提交 Git。

这是源码补交付，不替换电脑上运行的 1.0.1，不创建新的更新服务。更新器接口/包格式由 Mac 整合时明确设计和测试。
