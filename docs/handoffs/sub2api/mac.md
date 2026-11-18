# Mac Sub2API 接入交接

记录时间：2026-11-18 18:58（Asia/Shanghai）。当前阶段：交接记录已准备，端到端接入仍待验证；推送结果以本次交付消息的实际提交链接为准。

## 来源与范围

- 本端分支：`codex/sub2api-mac`。
- 本次交接基于 `main` 的 `0f942f66e19904a327c1c5b76c3d102b422e2e5e`，只增加文档，没有修改或部署运行代码。
- 最近抓取的 Windows 分支：`f50828a1220cf80e2c2f1d6b60883d2c21526cb6`。这是所见仓库版本，不证明 Windows 现场部署或运行版本。
- 用户已将 Windows 排查提示词发给 Windows 端；本端尚未取得 Windows 新的现场结果或交接提交。
- 私有连接资料、本机配置、备份与日志各自保留在本机，不进入本记录。

## 已做与实际证据

| 时间与范围 | 操作或结果 | 结论 |
| --- | --- | --- |
| 2026-11-18 凌晨，本机检查 | Mac arm64；当时 CC Switch 4.0.34、Codex App 26.1111.21308（27194）；包内 arm64 frpc 0.72.0 及 visitor 配置校验通过 | 历史版本与配置检查通过，后续切换前需复查 |
| 2026-11-18 02:45，本机 visitor 日志 | `login to server success`、`start visitor success`；请求健康检查后返回 `custom listener for [sub2api_ke] doesn't exist` | 当时可登录 frps，但目标 provider 未在该连接目标上注册；不能据此认定 Windows 服务本身故障 |
| 2026-11-18 凌晨，CC Switch 界面保存并只读核对 | 已独立保存 `Windows Sub2API`；下游 Key 在本机比对一致 | 供应商已保存，尚未切换；未改原使用中供应商 |
| 2026-11-18 18:58，本机实时复查 | `lsof` 未发现 18081 listener；回环 `/health` 请求失败，类型 `URLError` | 当前 Mac visitor 不在监听，本次没有重新启动或改变供应商 |

已保存的供应商关键字段：

```toml
model_provider = "custom"
model = "gpt-6.1-sol"

[model_providers.custom]
base_url = "http://127.0.0.1:18081/v1"
wire_api = "responses"
```

以上是无凭据字段摘录，不是整份 Codex 配置。当前 CC Switch 保存的供应商 auth 对象含专用 `OPENAI_API_KEY`，生成 `requires_openai_auth=true`；尚未切换，因此最终认证文件或 bearer 字段及 App 请求是否正确必须另验。后续由 CC Switch 按实际版本管理，不能照搬连接包旧 `env_key` / `Launch-Codex.command` 方案。

## 尚未通过的验收

| 验收项 | 状态 |
| --- | --- |
| STCP 实际转发 | 未通过；历史为 provider 缺失，最新本机 visitor 未运行 |
| visitor `/health` | 最新失败 |
| 下游 API Key 认证 | 只做了本机文件一致性比较；通过隧道的认证未验证 |
| `/v1/responses` 流式完成 | 未执行 |
| CC Switch 切换为“使用中” | 未执行 |
| 重启 Codex App 后新聊天真实回复 | 未执行 |

## Windows 接收方下一步

1. 在 Windows 原部署副本中检查 Sub2API 和专属 frpc，先验证 `http://127.0.0.1:8080/health`。
2. 使用原部署的 `Start-Sub2API.cmd`，再用 `Status-Sub2API.cmd` 检查健康、进程归属及 `sub2api_ke` 注册。与 Mac 配套的 frps/secret 仅在本机比较，不发送或公开凭据。
3. 如已启动仍失败，记录确切脱敏错误与发生时间，分清登录、注册、后端连接各阶段；保留 Windows 本地已成功的 CC Switch 链路。
4. 在 `windows` 分支提交 `docs/handoffs/sub2api/windows.md`，写实际测试代码 SHA、现场结果和未验收项；推送后以提交链接交接。Windows 分支初始不含模板，请先从 Mac 的准确交接提交读取 `docs/handoffs/sub2api/windows-template.md`，接收命令见本目录 README。

## Mac 接回后的下一步

读取 Windows 的准确交接提交并核验其范围；检查 18081 防止重复启动，再运行私有连接包的 `Connect.command`。只操作这条 Sub2API visitor，不终止 Ollama 或其他 frpc。

先过健康、Key 认证和模型列表，再发一次最小原生 Responses 流式请求。模型不可用时使用网关实际模型列表选择。API 通过后才由 CC Switch 切换供应商，核对实际 Codex 配置目录和最终认证，保留官方登录、MCP、权限及个人设置；最后重启 App 并验收新聊天真实回复。

日常次序为 Windows Docker/Sub2API/provider → Mac visitor → CC Switch → Codex。停止连接前切回可用供应商，再在 Connect 终端按 Control-C；Mac 重启后重新运行 Connect。本次没有新增登录自启或 Windows 操作。
