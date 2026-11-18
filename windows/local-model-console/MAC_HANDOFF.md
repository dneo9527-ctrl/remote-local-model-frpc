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
