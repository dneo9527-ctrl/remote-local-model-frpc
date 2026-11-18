# Windows Sub2API STCP 交接

本记录只交接 Sub2API 的 Windows provider。Windows 在本仓库 `windows` 分支维护，Mac 在自己的 `codex/mac-receiver` 分支继续。读取或合并前先检查工作区，不自动修改另一端分支，不把 Git 源码目录当作当前运行目录。

## 2026-11-18 检查结果：Windows 已恢复，provider 已注册

Mac 报错 `custom listener for [sub2api_ke] doesn't exist` 后，Windows 端实查结果：

| 项目 | 实际结果 |
| --- | --- |
| 原部署归属 | Docker 的 `com.docker.compose.project.working_dir` 和 `config_files` 确认为原工作区的 `outputs/sub2api-local`；不是 Git 仓库里的模板目录 |
| 原容器 | Sub2API、PostgreSQL、Redis 均为原容器，创建时间仍为 2026-11-17，原命名卷保留 |
| 容器内健康 | 三个容器均已恢复 `healthy` |
| Windows 回环健康 | `http://127.0.0.1:8080/health` 返回 HTTP 200、`{"status":"ok"}` |
| 实际端口映射 | 已恢复原 `127.0.0.1:8080:8080`，未开放公网或局域网监听 |
| provider 进程 | 专属 frpc PID `21596`，2026-11-18 19:04:07 +08:00 启动；PID 仅代表本次检查，后续须重新识别 |
| frps 登录 | 2026-11-18 19:04:08.398 +08:00，新日志确认成功 |
| STCP 注册 | 2026-11-18 19:04:08.571 +08:00，新日志确认 `sub2api_ke` 注册成功 |
| 其他隧道 | 原 Ollama 隧道进程及其配置保留，未终止 |
| 私有配置 | provider 与 Windows 保存的原 Mac visitor 资料逐项比对一致；未访问 Mac 当前实际文件 |
| Mac 端验收 | 未验证，由 Mac 分支继续 health、认证、Responses 和 Codex App 实测 |

本次 provider 启动后的脱敏日志证据：

```text
2026-11-18 19:04:08.398 [I] [client/service.go:389] [session] login to server success, get run id [session]
2026-11-18 19:04:08.398 [I] [proxy/proxy_manager.go:218] [session] proxy added: [sub2api_ke]
2026-11-18 19:04:08.571 [I] [client/control.go:174] [session] [sub2api_ke] start proxy success
```

这是检查时的运行状态，不是永久在线保证。Docker 和 frpc 均需保持运行，Windows 需保持联网且不休眠。

最终复查为 2026-11-18 19:06:18 +08:00：同一 provider 已持续运行 128 秒，仅一个实例、一条已建立连接，启动后没有新的失败或重连日志。重复运行原启动脚本仍复用同一 PID 和创建时间，未产生重复隧道；三个原容器健康、Windows 回环健康再次通过。

### 已处理的 Docker 启动故障

首次运行原 `Start-Sub2API.cmd` 时，Docker 因运行时残留的 `docker-secrets-engine/engine.sock` 无法访问而退出。使用 Docker 自带停止命令关闭本次失败启动，核对该目录只包含一个零字节套接字后，将目录整体保留改名，再次运行原启动脚本，Docker 与原三个容器已经启动。

该处理未删除目录、未重置 Docker、未重建容器或账号池。原运行时目录保留在：

```text
%LOCALAPPDATA%/docker-secrets-engine.provider-repair-20261118-185410
```

同类现象参考：[Docker 官方问题追踪 #15064](https://github.com/docker/for-win/issues/15064)。本次结论依据本机日志及实际启动结果，不把问题讨论中的解决建议视为本机已完成动作。

### 已恢复的端口映射故障：Windows 排除端口范围包含 8080

Windows 实查 TCP 排除范围包含 `8056–8155`，覆盖 8080。直接尝试绑定 `127.0.0.1:8080` 也失败；没有其他监听进程占用该端口。

Docker 后端脱敏错误：

```text
exposing port TCP 127.0.0.1:8080 -> <container>:8080:
listen tcp4 127.0.0.1:8080: bind:
An attempt was made to access a socket in a way forbidden by its access permissions.
```

管理员修复在用户完成 UAC 后，于 19:03:38–19:03:48 +08:00 实际执行成功：重启一次 WinNAT 后，8080 可重新绑定；仅重启原 `sub2api` 应用容器以恢复回环端口映射。前后容器 ID 和卷一致，WinNAT 恢复 `Running`。没有修改永久动态端口范围、排除范围、Compose、frp 凭据或防火墙。

随后从原部署运行 `Start-Sub2API.cmd`，先确认回环健康，再启动专属 frpc；`Status-Sub2API.cmd` 已执行，显示原三个容器健康、`Local health: ok` 和该 provider 进程。检查期间再次确认同一 PID 持续运行、无重复 provider、无新的重连或注册失败日志。

### Windows 本机验证与恢复资料

以下路径以原工作区为基准，实际路径由 Windows 端记录保管：

1. Docker 容器标签确认原目录，原容器创建时间和卷名确认保留原部署；未从 GitHub 模板重新初始化。
2. 原工作区 `work/provider-repair-admin-result.json` 记录本轮实际管理员操作和前后状态；同一报告复制进本机备份目录的 `port8080-recovery.json`。
3. `work/Restore-Sub2ApiPort8080.ps1` 是本次本机恢复脚本，依赖同目录的诊断记录；未上传，也不是面向新部署的通用安装脚本。日常启动仍使用原部署里的 `Start-Sub2API.cmd`。
4. provider 的程序路径与 `-c` 参数均指向原部署专属文件，实际配置为 `sub2api_ke`、STCP、`127.0.0.1:8080`；TLS 和 STCP 加密保留。地址、端口、认证与密钥已在本机和保存的 visitor 资料比较，均一致，不公开其值。
5. 同类排除端口问题在未来重启后是否复发尚未验证；本次未添加永久网络策略、开机任务或后台看守。若再次发生，先诊断，避免反复启动 frpc 或重建数据库。

原 `.env`、Compose、启停脚本、frpc 配置与旧 PID 文件均已在本机备份，目录为原部署下：

```text
backups/provider-repair-20261118-185129/
```

修改前后文件哈希核对表明原 `.env`、Compose、frp 配置、启停脚本、本地 Codex 配置及 CC Switch 设置文件未变；只有运行态 `provider.pid` 随启动更新。CC Switch 数据库正被其程序使用，未直接读取或复制，未声称已对数据库内容做验收。

### 给 Mac 端的下一步

Windows 本轮已注册成功，现在在 Mac 现有 visitor 上重新检查：

1. `http://127.0.0.1:18081/health`。
2. 使用本机保存的 Sub2API 下游 Key 验证 `/v1/models` 和最小 Responses 流式请求。
3. 在 Mac CC Switch 的 Codex 自定义供应商中使用 `http://127.0.0.1:18081/v1`、Responses 协议和该下游 Key，切换后重启 Codex App 实测。

这些 Mac 检查留给 Mac 端执行。如仍出现 listener 不存在，先请 Windows 复查本轮之后的进程与日志，并核对 Mac 实际使用的 frps 及 `serverName`。不要因一个旧 PID 或历史日志推断当前在线，也不要重新生成原密钥。

只读取 Windows 交接而不合并代码：

```bash
git fetch origin
git show origin/windows:windows/sub2api-local/MAC_HANDOFF.md
```

需要合并时，在 Mac 自己的分支保存本地工作后合并 `origin/windows`，保留 Mac 端已有改动；Windows 端不会修改 Mac 分支或 `main`。

### 日常启停

- 从原部署目录双击 `Start-Sub2API.cmd` 启动，双击 `Status-Sub2API.cmd` 检查。
- `Stop-Sub2API.cmd` 会停止本项目 provider 及三个容器，数据卷保留，本地 CC Switch 也会暂时无法调用该网关。
- 仅断开远程隧道而继续本地使用时，在原目录 PowerShell 执行 `. ./Tunnel.ps1; Stop-Sub2ApiTunnel`。
- 本项目未新增启动任务或后台看守，Windows/Docker/frpc 状态仍需在重启后确认。
