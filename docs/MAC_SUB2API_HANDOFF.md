# Mac Sub2API 连接与 CC Switch 交接

检查时间：2026-11-18 22:29–22:34 +08:00。Mac 分支为 `codex/mac-receiver`。已 fetch 并阅读 Windows 提交 `6e9735f4a2bf992c90efa817e4509b3d4f3c6842` 的 `windows/sub2api-local/MAC_HANDOFF.md`，未合并或修改 Windows 分支。

## 实际结果

| 项目 | 本轮 Mac 结果 |
| --- | --- |
| 已安装控制台 | `0.2.0-preview.1`，实际运行发布源码；现有 Ollama 连接保留 |
| Sub2API 启动 | 通过现有控制台的 `/api/action` 后端选择原 `private-stcp`，启动其管理的 visitor；首页目前没有该入口 |
| 私有配置 | 引用原交付的 visitor；`stcp`、`sub2api_ke`、`127.0.0.1:18081`、TLS、STCP 加密核对一致；frpc `0.72.0` |
| frps 控制连接 | 22:29:52 登录成功，visitor 监听成功；没有新增或改动 Ollama visitor |
| health | 22:30:01 请求被重置；对应日志为 `custom listener for [sub2api_ke] doesn't exist`；22:30:15 复查仍失败 |
| 下游 Key / models | 本轮未验证：health 失败后停止验收；不能以 Windows 历史测试替代 |
| Responses 流 | 本轮未验证 |
| CC Switch | `4.0.35`；现场未找到原 Windows 供应商，已通过界面重新保存独立的 **Windows Sub2API**，下游 Key 与专用本机文件一致 |
| 保存的供应商配置 | `custom` 与 provider 表一致；`http://127.0.0.1:18081/v1`、`wire_api="responses"`；暂存模型 `gpt-6.1-sol`，当前 Windows 模型可用性仍待接口实测 |
| CC Switch 切换 | 尚未切为使用中，当前 XR 继续生效；保存后实际 Codex 配置逐字节未变 |
| Codex App | **未验证**；由用户正常退出、重开后，在新聊天中发送真实请求 |

Windows 历史交接证明其 19:31 曾经通过公网 frps 验证健康与认证模型列表。本轮 Mac 结果表明同一目标 listener 在检查时不可用；无法仅凭此结果区分 Windows 已停机、provider 未运行或远端连接约定发生变化。Mac 未代为操作 Windows，也没有修改地址、端口或重建服务。

Mac 私人配置、CC Switch 数据库及 Codex 配置均已在原本机工具备份目录保留。交接文件不包含私有配置、Key、frps 地址、认证值、数据库或原始日志。

## 恢复验收

1. Windows 双击原桌面 **启动 Sub2API**，确认同一 Docker 项目 `sub2api-local` 的四个容器运行，原三个服务健康，`sub2api-frpc` 有新的登录及 `sub2api_ke` 注册成功日志。容器内目标应为 `sub2api:8080`。
2. 核对双方仍使用原来同一 frps 与配套 STCP 配置；不公开其值。Mac 原 visitor 已启动时复用，不再启动第二份。
3. Mac 依次测试 `/health` 的 `status=ok`、无 Key 拒绝、有 Key `/v1/models` 返回 JSON；从实际模型列表选择模型，再完成 `/v1/responses` 的内容增量与 `response.completed` 验收。
4. 接口通过后在 CC Switch 的 Codex 分类切换 **Windows Sub2API**，检查实际写入 `~/.codex` 的地址、协议及下游 Key 认证；保留其他配置。供应商保存的 `requires_openai_auth=true` 是当前版本生成值，切换后如何写入 Key 尚未实测，不能据此声称有效请求已通过。
5. 用户正常重启 Codex App，创建新聊天，发送简短请求。真实收到回复后单独记录 App 验收；不要用 models 或登录状态代替。

## Mac 需要的小幅完善

现有隧道后端可以复用，无需因 Windows provider 改用 Docker 而重写。当前缺少的是面向日常使用的入口和验收：

- 给控制台增加独立的 **Windows Sub2API** 状态与连接开关，沿用现有 Tunnel 管理；将“frps 登录成功”和“Windows API 可用”分别显示。
- 提供打开 CC Switch 的入口；供应商、Key 和 Codex 有效配置由 CC Switch 管理。不要接回控制台旧的 Codex 配置写入流程。
- 启动或切换提示应指向 CC Switch；18081 被外部 visitor 占用时保留原进程并明确提示，避免重复启动。

上述界面完善是待开发项，本次只完成连接后端实测、供应商保存与交接记录，未修改或发布控制台程序。

## 日常最少步骤与回退

入口完善并且真实验收通过后，日常顺序为：Windows 启动 Sub2API → Mac 控制台开启 Sub2API 连接并确认 API 可用 → CC Switch 选择 Windows Sub2API → 打开 Codex。供应商未变时无需每天重新配置 Key；切换供应商后按当前 App 要求重启。

**当前首页尚无 Sub2API 开关，不能声称已具备上述一键流程。** 当前 visitor 由控制台管理；可通过同一 `/api/action` 后端的 `sub2api/stop` 停止，或退出控制台使其清理所属 visitor。关闭网页不等于退出控制台；独立 Ollama 服务保持原状态。重开控制台不会自动启动 Sub2API visitor，仍需显式启动。

若只需恢复已有 Codex 服务，在 CC Switch 选择此前的 XR 或其他原供应商，再按需重启 Codex。当前 XR 本轮未切离。Windows 停止整个项目会影响 Windows 本机使用，操作前自行确认；停止时保留数据卷。
