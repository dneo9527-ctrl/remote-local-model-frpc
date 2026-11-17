# 本地模型控制台 / VPS 切换面板（Windows 1.0.2 整合预览）

这是 Windows 电脑当前使用的原生面板源码：**C# / WPF 界面 + PowerShell 5.1 后端**，不是 Sub2API 部署页。保留一键启动、停止本项目隧道、状态刷新、Cherry Studio 入口、线路增删改、Ping/TCP 延迟、国旗与位置、连接说明、日志入口。

Mac 端可以直接阅读和编辑这些文件、整合功能与视觉设计；WPF 程序本身只在 Windows x64 运行。Mac 整合和更新器继续由 Mac 侧维护，见 [交接说明](MAC_HANDOFF.md)。

## 源码入口

| 文件 | 用途 |
| --- | --- |
| `LocalAI-Control.cs` | 窗口逻辑、线路、测速、状态、进程间单实例 |
| `LocalAI.UI.xaml` | 完整界面布局与样式 |
| `LocalAI-GUI.ps1` | 服务/配置操作后端，及兼容启动入口 |
| `build.ps1` / `app.manifest` / `flags/` | EXE 编译与内嵌资源（含本版安全启动辅助脚本） |
| `scripts/start-services.ps1` | STCP 启动辅助：复用健康 Ollama，未运行时只启动回环监听，不改 Tailscale/防火墙 |
| `启动本地模型.bat` | 原双击入口，优先使用 EXE |
| `examples/` | 无真实地址、无凭据的配置示例 |
| `check-*.ps1` | 配置、恢复、界面回归检查 |

来源为已上传的 1.0.1；本候选组件版本 1.0.2，保留原生界面与配置路径，增加 STCP 默认安全启动和共享发布入口。来源电脑的运行中 EXE、配置和服务未被本次源码整合覆盖。

## 构建与打开

Windows x64，需 Windows PowerShell 5.1 与 .NET Framework 4.8；使用系统自带 .NET Framework 编译器，不依赖 Pinokio、Python、Docker 或 npm。

在本目录的 Windows PowerShell 中执行：

```powershell
& .\build.ps1 -OutputDirectory 'D:\YourPrivateRuntime\console-build'
& 'D:\YourPrivateRuntime\console-build\LocalAI-Control.exe' --project-root 'D:\YourExistingModelProject'
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
- 新版启动只复用健康 Ollama；没有监听时以 `127.0.0.1:11434` 启动。已有监听但 API 异常时拒绝接管，不停止共享进程、不改防火墙或 Tailscale。原来已配置的远程监听若健康则保留，不能把复用说成已收紧现有监听。
- 面板后端和内嵌启动辅助都要求确切 EXE 及单个绝对 `-c` 配置路径；不接管归属不明的相对路径进程。EXE 使用本版本内嵌辅助脚本，未调用既有项目中可能过时的启动脚本。
- 原面板约每 6 秒创建后台 PowerShell 刷新状态，约每 30 秒测速；本次只补源码，不声称已完成低开销刷新改造。
- 默认只使用内嵌/已有国旗与手动位置，**不**把 VPS IP 发往第三方地理库。如本人需要此功能，先了解会向 `ipwho.is` 发送线路解析 IP、向 `flagcdn.com` 请求国旗，再在私人 `config/panel-preferences.json` 明确写入 `{"allowExternalMetadata":true}` 并重开。位置不能保证真实机房。
- `127.0.0.1` 始终指当前客户端设备，Mac/手机各自需要 visitor；模型端口和管理端口不能混用。
- TCP 测速读取当前 `frpc.toml` 顶层 `serverPort`，仅省略该键时使用 frp 默认 7000；显式无效/重复值拒绝测速。当前线路仍只替换地址，不自动迁移不同节点的端口、token 或 secretKey。
- `model-settings.json` 缺失或无效会给出受控错误，不创建/覆盖私人配置。

## 检查

在隔离目录执行以下检查，不会运行真实服务启停。UI 用例构造临时窗口但不显示正式窗口。

```powershell
& .\check-panel.ps1
& .\check-switch-recovery.ps1
& .\check-stcp-safety.ps1
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

“操作记录与日志”中新增“检查更新”，打开本仓库 Releases；它本身不下载或替换程序。共享源码更新器在选择并校验源码后，从该版本构建 EXE 到源码外的私人 runtime。关闭旧窗口再启动新版；不覆盖项目 config/data、模型权重或共享服务。未新增更新常驻服务。Windows 实机服务/多 VPS 切换仍需现场验收。
