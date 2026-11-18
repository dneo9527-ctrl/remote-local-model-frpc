# 给 Mac 整合方

原面板源码在本目录，重点阅读 `LocalAI.UI.xaml`、`LocalAI-Control.cs` 与 `LocalAI-GUI.ps1`。这是 WPF 原生窗口，不是网页；仅 `scripts/start-services.ps1` 不足以复原界面。

本次只添加原面板可维护源码、完整内嵌资源、参数化检查及占位示例；同仓库 `windows/sub2api-local/` 仍是另一套部署脚本，不要混为同一个 UI。

```bash
git fetch origin
# 先提交/暂存自己的改动，在接收 Windows 源码的分支合并：
git merge origin/windows
```

Mac 可以编辑这些文件，但不能本地运行 WPF 或 .NET Framework 编译。请保留 Windows 适配器，在共享界面中实现相同布局与行为；Mac 启动和进程控制使用本机真实路径，不能套用 Windows 路径。

应保留：任意数量线路、选择与已应用线路区分、状态/错误提示、Ping 与 TCP 分层、国家/城市可编辑、切线只改地址、条件恢复、确切进程归属、保留共享模型、默认折叠连接信息。

需继续改进的已知点：6 秒后台 PowerShell 轮询的成本；原服务辅助脚本的远程 Ollama 监听和较宽相对配置归属；模型档案尚主要是配置读取；诊断导出/更新器/跨机管理不在旧 1.0.1 中。不要因源码已上传而标注这些功能已验收。

Windows 真实截图请从用户已连接的 UU 远程画面获取，保留真实状态；截图前不要切线或主动加载模型。此仓库不提供伪造截图或携带个人机器状态的素材。

更新器应分别处理 Windows EXE 与 Mac 应用的构建、哈希、关窗替换及回退；包中排除配置与凭据。不要直接把构建脚本存在当作签名、下载校验或自动更新已经通过。

## 2026-11-18 整合候选

本候选已将启动辅助改为默认 STCP 回环启动、共享 Ollama 复用、绝对进程归属；当前 EXE 内嵌安全辅助，不依赖原项目旧辅助。默认关闭 IP 地理库/国旗联网；测速采用真实 serverPort。新增同库 Releases 入口和外部 build 输出。原 6 秒刷新与只改 serverAddr 的线路模型仍保留；不同端口/密钥的完整 VPS 档案未在 Windows 实现，不能把 Mac 独立 visitor 档案能力当作 Windows 已完成。

## 2026-11-18 Windows 1.1.0 接口与布局

已实现固定右上启动、卡片独立停止、每线路 visitor 复制、紧凑客户端入口与底栏弹窗。状态轮询改为 15 秒，最小化/隐藏时跳过；线路探测仍约 30 秒。

偏好字段：`ollamaPort`（默认11434）、`visitorPort`（默认11436）、`webChatUrl`（默认空），保留 `allowExternalMetadata` 和未知字段。模型端口同步 unique name=ollama/type=stcp 的 localPort，frps 端口为顶层 serverPort。切线仍只改 serverAddr。端口事务有备份、哈希日志与启动阻断；服务操作及保存使用同一 Windows Mutex。

Mac 整合应保持相同设置含义，但用本机原有锁与进程适配器；Windows 设置页不远程改 VPS。visitor 复制不包含 Windows 本机路径，接收端 loopback 与端口为自己的地址。Windows 网页入口可配置；不会因按钮存在便安装/启动 WebUI。

此前 6 秒轮询及只读端口的描述属于旧版。新增功能的 Windows 隔离验收与本机只读窗口检查见 VALIDATION；未执行真实停模型、多 VPS 切线或 Mac 原生验收。

## 2026-11-19 · 1.1.1 模型与卡片入口

默认模型标签可点开已安装模型下拉，Status 新增 Models 名称数组，使用当前 OllamaPort 的 /api/tags。保存前重新读取本端口模型列表，精确验证名字，原子更新 model-settings.json.defaultModel，保留 profiles/context/hardware/providers/未知字段并备份。当前简写默认值可匹配同名 :latest 标签，不忽略名字大小写。没有已安装的当前项时需用户主动选择，不擅自预选另一个。离线/空列表不改配置；保存不触发推理、下载或更改客户端的请求模型。

Ollama/frp 详情点击共用原设置窗，分别聚焦模型/frps端口；Tailscale 详情为只读信息与复制IP。两端整合请保留这些操作语义，不能将网络状态当成模型已可通过 Tailscale 访问。新增 check-model-choice 覆盖未知字段保留、冲突拒绝、无效/缺失模型和 latest 兼容。

## 2026-11-19 · 1.1.2 视觉与全局停止

运行时主按钮执行StopAll，逐项停止确切归属的本项目frpc和当前端口Ollama serve，再核对退出；返回部分失败，不追杀重启/未知进程。Status新增OllamaListening，不能用API离线推断进程已停。Tailscale由系统管理。

选中未应用线路时主按钮应用并切线，同时在横幅保留停止当前服务；部分运行提供独立补齐启动，避免主按钮对相同状态交替表示停止/启动。模型停止使用同风格确认窗，默认取消，Esc/Enter取消。独立frp停止仍保留。界面为浅色工具台、细状态点、无粉框局部停止，输入与模型下拉控件也统一。Mac整合保持这些状态语义，不套用Windows CIM/Mutex实现。

新增check-stop-all、check-stop-ui及check-stop-processes；最后一个实际编译并结束自己创建的临时无业务进程，验证端口/路径归属并保留同名无关进程，不使用真实Ollama/frpc或个人配置。
