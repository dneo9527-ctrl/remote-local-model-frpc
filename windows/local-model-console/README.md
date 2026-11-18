# 远程模型控制台 · Windows 1.1.1 预览

Windows 原生 C# / WPF 窗口与 PowerShell 5.1 后端。Windows 提供 Ollama，Mac、手机或另一台 Windows 通过自己的 frpc visitor 接收。保留已有模型、配置、线路管理、延迟检测、国旗位置和 Cherry Studio 入口；不依赖 Pinokio、Docker 或 Python。

## 使用

1. 双击原项目的 `启动本地模型.bat`，右上角“一键启动”启动模型和当前线路。选中另一线路后，点右上角“应用”才切线。
2. Ollama 与 frp 卡片各有独立停止按钮。停止隧道只影响本项目；停止模型会中断该 API 上的所有本机及远端问答，需确认并通过进程归属核验。托盘主管可能重新拉起 Ollama，本面板不会追杀新进程。Tailscale 仅显示系统状态。
3. 每条线路右侧“复制配置”将该节点完整 visitor TOML 放到剪贴板，文本顶部标注线路名，直接粘贴到接收设备的配置编辑器。复制不会切线，也不生成文件。文本含连接密钥，请私下保管；不要上传 Git。复杂、动态凭据或非本项目 STCP 配置会拒绝复制。
4. 底栏“设置”可修改下表字段。“连接信息”“日志与记录”打开可滚动的小窗口，Esc 关闭。
5. 点击右下“默认模型 ▾”，从当前模型服务的已安装列表选择并保存。保存前重新确认模型存在，只更新项目 defaultModel，保留上下文档案、硬件与厂商等其他字段；不会下载、加载、卸载或重启模型。Cherry / Mac / 手机客户端仍使用自己请求中选择的模型。API 离线时先修正端口/启动，再刷新；不把离线误报为模型清空。
6. Ollama/frp 卡片的下方信息可以点击，直接进入端口设置并聚焦对应字段；Tailscale 下方信息打开只读说明与复制 IP。IP 由 Tailscale 分配，在系统客户端管理。
7. “打开网页版”打开已配置聊天地址；未配置时仅识别本机共享控制台 18765 的 `/chat`，否则引导设置。Ollama API 根地址本身没有聊天页面。打开链接不表示网页服务已经可用。

| 设置 | 默认 | 生效方式 |
| --- | --- | --- |
| 模型 API 端口 | 11434 | 同步 `panel-preferences.json` 的 `ollamaPort` 与 Ollama STCP `localPort`；点击启动后使用 |
| frps 服务端口 | 7000（以当前配置为准） | 只修改顶层 `serverPort`；需与 VPS 及 visitor 一致 |
| visitor 接收端口 | 11436 | 下一次复制使用，客户端填接收设备自己的回环地址 |
| 网页聊天地址 | 空 | 可填已部署的 Open WebUI 或共享 `/chat` URL，保存后立即使用 |

修改模型/frps 端口前，先停止本项目 frp，再保存设置并点击启动。不会自动停止旧模型端口上的服务；旧端口实例可能仍占资源，按需单独管理。远端需要重新复制匹配配置。端口冲突在启动时据实拒绝接管。端口保存会保留备份；发生中断时重新打开设置恢复，未知外部变化保留记录并阻止启动。

普通线路切换仍然**只改 `serverAddr`**，不改 token、secretKey 或端口。设置页的显式端口修改与切线是不同操作。三台 VPS 如端口不同，先按目标实际端口保存；本版没有为每个节点保存独立认证档案。

## 源码与构建

Windows x64、Windows PowerShell 5.1、.NET Framework 4.8。系统编译器直接构建，PS1 使用 UTF-8 BOM，C# 使用 UTF-8。

```powershell
& .\build.ps1 -OutputDirectory 'D:\PrivateRuntime\console-build'
& 'D:\PrivateRuntime\console-build\LocalAI-Control.exe' --project-root 'D:\YourModelProject'
```

已有项目结构：

```text
YourModelProject/
  config/frpc.toml
  config/model-settings.json
  config/panel-preferences.json     # 可选，设置页保留未知字段
  scripts/start-services.ps1
  bin/frp/bridge.exe                # 官方 frpc，需支持 verify -c
  tools/control-panel/LocalAI-Control.exe
  启动本地模型.bat
```

`LocalAI-Control.cs` 管理主窗口；`LocalAI.UI.xaml` 是布局；`PanelWindows.cs` 管理设置/日志/连接弹窗与配置复制；`PanelConfig.cs` 保守修改端口；`PanelExport.cs` 生成 visitor；`LocalAI-GUI.ps1` 是服务后端。EXE 内嵌当前安全启动辅助，启动只复用健康模型，未监听时仅启动回环地址。已健康的旧监听不会被偷偷收紧或重启。关面板保留服务。

新项目先从 [Ollama](https://ollama.com/) 与 [frp 官方发行](https://github.com/fatedier/frp/releases) 安装依赖，再私下配置。`examples` 全为占位值，不可直接连接。位置来自手动标注/已有信息；外部 IP 地理库默认关闭。Tailscale 在线、Ping/TCP 成功均不能代替模型端到端问答验收。

## 检查与交付

仓库根目录运行：

```powershell
powershell.exe -NoProfile -STA -File .\tests\windows\Verify-Console.ps1 -OutputDirectory 'D:\PanelChecks'
```

九组原生检查覆盖字节保留、文件冲突、恢复、进程归属、导出与隐藏 WPF。可为 `check-panel.ps1`、`check-export.ps1` 额外传 `-FrpcExecutable`，仅用官方 frpc 解析临时夹具，不连接服务器。具体结果见 [验证记录](VALIDATION.md)。Mac 可以编辑源码；WPF 本身只能在 Windows 运行，见 [Mac 交接](MAC_HANDOFF.md)。

发布包排除真实 config、data、state、模型、密钥、日志与 visitor。共享更新器从准确源码构建到源码外缓存；“检查更新”只打开仓库 Releases，不假装自动升级或签名已经通过。
