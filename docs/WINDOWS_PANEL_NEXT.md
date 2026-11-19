# 给 Windows 端的实施提示词

请在 Windows 本机继续改进已部署的远程模型控制台，并通过同仓库不同分支与 Mac 交接。

仓库：`https://github.com/dneo9527-ctrl/remote-local-model-frpc`
Windows：`windows`；Mac：`codex/mac-receiver`。先检查 Git 状态、备份现用安装与配置，再 fetch 并阅读双方最新交接。保留已有改动，不覆盖对方分支，也不把 Git 源码目录误当运行目录。

## 任务与顺序

1. **原面板增加 Sub2API 独立卡片。** 复用原部署的 Start/Status/Stop-Sub2API 脚本及 `sub2api-local` Docker 项目，显示四容器状态、本机 health、`sub2api-frpc` 当前注册状态和最近检查时间。识别原 Docker 部署目录，不从模板重建。启动、停止分别独立于 Ollama；保留原“启动 Sub2API”快捷方式。全局停止需明确影响 Ollama、模型隧道还是 Sub2API，按用户所选范围执行并逐项核对，失败保留重试入口。
2. **统一分层诊断。** 区分进程存活、端口监听、frps 登录、provider 注册、health、认证及真实模型请求。Ollama provider 与 `sub2api_ke` 分开显示；Tailscale 在线、TCP/Ping、旧注册日志均不能代替 Sub2API 当前可用。只读取当前进程/当前连接开始后的日志，结果显示时间与过期状态。提供不含地址、Key、token、原始日志或用户正文的诊断摘要。
3. **每个 VPS 使用完整独立连接档案。** 现版仅切 serverAddr，需支持每节点的地址、控制端口、provider 名、TLS/加密与本机私有凭据引用。按 [VPS_PROFILE_SETUP.md](VPS_PROFILE_SETUP.md) 的固定约定配置新 VPS 后，日常允许复制原方案只改 IP；同时保留控制端口、provider、token、secret 等高级编辑，不能锁死其他参数。真实凭据仅在本机保存，不进入线路列表、仓库或诊断导出。保留已选/已应用区分、端口事务、并发冲突拒绝和失败回滚。切线仍由用户触发，不能后台擅自改路由；不能保证其他客户端未在生成时，应明确提示中断影响。visitor 复制须与所选档案一致，私下传递，不上传。
4. **模型与界面保持既有功能。** 保留真实已安装模型下拉、项目默认值、独立停止、补齐启动、日志/设置小窗和现有浅色排版。Windows 默认模型不等于 Mac/Cherry/CC Switch 的模型，不能强制联动修改。可增加托盘入口以便确认后台状态、重新打开和明确退出；关窗口与停止服务的区别要写清。不要因界面整合自动关闭系统 Tailscale。
5. **测量后优化状态刷新。** 当前已是15秒、隐藏或最小化暂停。核对 PowerShell/CIM/Tailscale 调用的实际开销，能合并就合并，低频详情与轻量状态分开；不要为刷新另加常驻看守服务。

## 保留条件

- 不删除模型、Docker 数据卷、账号池、下游 Key、原 frp 资料、CC Switch/Codex/Cherry 配置与聊天。
- Sub2API 本机仍绑定回环地址。Docker provider 的内部目标是 `sub2api:8080`，provider 名 `sub2api_ke`；原 frps、TLS 与 STCP 加密保留。Mac visitor 为 `127.0.0.1:18081`，CC Switch 使用 `/v1` 与原生 Responses；不改成 `/openai/v1`，不重复 `/v1`。
- 若支持 Tailscale，Windows 服务绑定回环地址时必须做明确的私有转发，不能声称直接连 Tailscale IP:8080 即可。优先原 frp 路线。
- 不按名称批量结束进程；核对路径、配置、创建时间和端口归属。保留现有共享 Ollama 和未知进程的保护。
- 不宣称代操作 Mac。Codex App 真实新聊天由 Mac 用户验收，不用 models 或 login status 替代。

## 验证与交付

先做隔离回归，再在原 Windows 安装上核验可见界面、一次实际启动/停止、单实例和退出范围。配置/Key/容器与卷应前后核对；演示状态不能替代真实结果。未测多 VPS、远端问答或 GPU 释放就明确未测。

更新 `windows/local-model-console/MAC_HANDOFF.md` 与 `windows/sub2api-local/MAC_HANDOFF.md`，写准确 SHA、当前操作入口、配置 schema、验收时间及边界。提交推送 `windows`，报告准确 commit 与 CI；不要推 Mac 分支，不创建重复部署。Mac 端会从 GitHub 读取交接，继续同一项目。
