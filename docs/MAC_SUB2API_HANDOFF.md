# Mac Sub2API 连接与 CC Switch 交接

## 当前联调：Mac 已就绪，等待 Windows provider（2026-11-19 01:12 +08:00）

用户确认服务器开启并要求继续，已恢复远端验收。Mac 已通过当前控制台开启原 `private-stcp`，复用原配置和端口：01:11:51 frps 登录成功、visitor 监听成功；01:12:12 `/health` 连接重置，对应新日志为 `custom listener for [sub2api_ke] doesn't exist`。尚未进入 Key、models 和 Responses 请求，CC Switch 也尚未切换到 Windows Sub2API。

01:14:33 通过控制台手动“检查 API”再次验证，仍为 `ConnectionResetError`；当前页面已缓存该失败结果，本机 visitor 继续运行等待复用。

已 fetch 并读取 Windows 当前 `6f38ba6acb49b8e1a45cb495004a17eeb34125f3`。Windows 模型面板与 `windows/sub2api-local` Docker 项目仍为独立入口；模型面板运行不能证明 Sub2API provider 已注册。当前报错表示所连接的 frps 上没有可用的同名 listener，仍需 Windows 核验当前运行状态与连接约定；不凭历史日志推断现在在线。

给 Windows 接续方：从**原部署目录**启动 `Start-Sub2API.cmd`，检查 `sub2api-local` 四容器运行及原三个服务健康；确认 `sub2api-frpc` 最新启动日志包含登录成功和 `[sub2api_ke] start proxy success`。容器内目标保持 `sub2api:8080`，核对原 frps/端口/配套认证，但不要提交这些私有值。请将本轮实际时间、测试结果与提交 SHA 写入 Windows 分支的 `windows/sub2api-local/MAC_HANDOFF.md`。Mac 没有操作 Windows、改端口或重建服务，现有 visitor 保留等待复用。

## 当前：全部退出（2026-11-19，preview.4）

用户明确纠正：退出就是关闭这个 App 的全部本机连接。已更新为 `0.2.0-preview.4`，菜单栏和网页均使用 **全部退出**：调用原 Ollama 管理器暂停网关、visitor 及其登录恢复，停止 Sub2API visitor，被动确认连接端口关闭后再关闭控制台和菜单栏。Windows 服务、模型、账号和私人配置不修改。关闭网页仍允许后台继续运行；真正全部退出后，下次打开 App 会显示连接已关闭，需要时再按开关恢复。

有连接未停止或本地状态无法确认时，返回失败并保留界面重试。退出期间拒绝新的启动、线路切换和聊天。成功收据与当前控制台实例绑定，菜单会等待后台及连接确实关闭，网页全部退出也会结束菜单进程。下面 preview.3 中“保留独立 Ollama”的退出语义已被用户纠正，不再适用。

本机已实际点击网页 **全部退出**（与原生菜单使用同一关闭接口），核实 `11435`、`11436`、`18081`、`18765` 均无监听，原 Ollama LaunchAgent 已卸载并暂停，控制台和菜单进程均已结束，退出收据匹配。重新打开时连接保持关闭，再次全部退出也成功。Codex、CC Switch、控制台、visitor 和启动配置的文件摘要未变。45 项 Mac 和 24 项更新/启动器检查通过，覆盖停止失败后的重试、配置缺失和禁止退出期间新启动/聊天。原生菜单逐项点按仍受无窗口自动化限制；不将网页点按声称为已代点原生菜单。远端健康和模型请求继续暂缓。

原 App 与源码完整备份保留在本机 `full-quit-*` 目录，准确路径见台账。回退代码不会自动重新开启已暂停的连接；用户按需从控制台开关恢复。

## 历史：菜单栏入口（2026-11-19，preview.3）

新增 `0.2.0-preview.3` Mac 本机候选：原 App 入口改为原生 AppKit 菜单栏程序，显示电脑小图标；菜单可打开控制台、在后台停止后重新启动，以及退出。关闭网页后菜单和后台继续运行，退出时只关闭本控制台及其管理的 visitor；独立 Ollama 连接保持原状态。未增加登录项或 LaunchAgent。

菜单持有正规启动器的前台子进程。它只读取回环 `/api/session`，核对实例与私人运行记录匹配后才请求关闭，并等待后台结束；状态不明确时保留图标供重试。重复打开会显示现有菜单，不新增后台。菜单异常结束时，所属后台检测父进程变化后清理退出，不凭 PID 文件去终止其他进程。

菜单构建与签名验证、41 项 Mac 隔离检查和 24 项更新/启动器检查通过；真实本机启动后确认菜单进程与后台父子关系、`preview.3` 与 session 实例一致，并实测菜单进程结束后 18765 自动关闭。窗口自动化工具无法读取无窗口 accessory App 的菜单，**直接点击菜单“退出”的自动化验收尚未完成**，不把进程记录当作已代点菜单。上游健康、认证、Responses 与 Codex 真实消息继续暂缓。

原 App 全量副本和本地源码均在本机本次 `menu-*` 备份目录保留，准确路径见工具台账。回退时先退出菜单，确认本控制台后台停止，再恢复该备份中的原 App 和 `source-before`；私人连接设置本轮未改。原已发布 `.1` 与更早基线继续保留，未创建新 Release。

## 本地段完成，远端验收暂缓（2026-11-19）

按用户“暂不测试，先完成本地段”的要求，本机已从原 **2本地 AI 控制台.app** 入口运行 `0.2.0-preview.2` 本地候选源码；这是 Mac 分支的本机安装，未创建 GitHub Release、未合并 main、未修改 Windows。

首页已按用户反馈收紧为两张并排连接卡，线路和长说明默认折叠，常用聊天/CC Switch 入口保留。实际 1280×720 窗口中内容高度从 1335 降至 541 像素，无横向溢出；折叠详情可正常展开。仅替换本地静态资源，无需重启服务或触发远端检查。

已完成：

- 首页独立显示 **Windows Sub2API** 连接开关、已保存连接选择、手动“检查 API”和“打开 CC Switch”。原 Ollama/Cherry/网页聊天入口保留。
- 状态刷新被动读取 Mac 本地监听，不接入 STCP 转发端口。Sub2API 健康请求仅由“检查 API”触发，显示的是上次手动结果；没有自动模型请求。当前设置 `automatic_health_checks=false`，原 Ollama 的自动健康检查也暂缓。
- 外部 visitor 占用 18081 时明确显示并禁用接管/换线路；仅停止本控制台所属 visitor。旧的控制台 Codex prepare/apply/restore 动作已拒绝，供应商和认证统一由 CC Switch 管理。
- 切换 Sub2API 连接前备份私人设置并检查已发生的并发编辑；不改 Ollama 配置。visitor 校验拒绝附加 includes、公网监听及公网管理端口。
- **Windows Sub2API** 供应商仍已保存，下游 Key 本机核对一致；本轮没有切换当前供应商。安装前后实际 Codex 配置和 CC Switch 设置文件摘要一致，原 Ollama 网关 PID 保留。

本轮只做本地验证：Mac 控制台 37 项隔离测试、更新器/启动器 24 项测试通过；隔离页面实点验证连接开关、手动检查结果与外部连接保护；实际安装页面确认新入口、`0.2.0-preview.2`、Sub2API 关闭、远端“未检查”。**未执行 Windows health、认证、Responses 或 Codex App 真实请求。**

### 当前使用与恢复

1. 现在可打开原 App 查看本地界面，进入 CC Switch 查看已保存供应商。Sub2API 按本轮要求保持关闭。
2. 日后恢复联调时，先启动 Windows 服务，再打开 Mac 控制台，选择 **Windows Sub2API · STCP** 并开启连接。按需手动检查 API。
3. 接口验收通过后，在 CC Switch 的 Codex 分类选择 **Windows Sub2API**，由用户正常重启 Codex 并发送新聊天消息。当前保存模型 `gpt-6.1-sol` 尚需当时网关实测确认。
4. 断开时关闭 Sub2API 开关；退出控制台也会清理它所属的 visitor。关闭网页不会停止连接。Mac 重启或重开控制台后需显式开启 Sub2API，未增加自启或后台服务。
5. 切回时在 CC Switch 选择原供应商，并按需重启 Codex。控制台不负责改写 Codex 配置。

### 本机安装与回退

原已校验发布版 `updates/releases/0.2.0-preview.1` 和原始基线均保留。本地候选安装在工具的 `source`，原 App 启动脚本未改；原 `updates/active.json` 已归档，因而正规启动器使用本地候选源码。私人设置留在原支持位置，备份不进 Git。

本机维护者回退时先退出控制台，将本次私人备份中的 `active-selected.json` 恢复为 `updates/active.json`，再从原 App 重开；启动器会重新校验并选用已保留的发布版。需要一并恢复本次本地设置时，先保存后续个人修改，再使用同一备份中的 `settings-before.json`。不要删除旧源码、账号池或私人连接资料。准确备份目录记录在本机工具台账。

下文为前一轮现场快照，不能当作本轮重新验证结果。

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
