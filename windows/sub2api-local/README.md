# Windows Sub2API：一个 Docker 项目，一键启停

`sub2api-local` 项目现在包含四个服务：`sub2api`、`postgres`、`redis`、`sub2api-frpc`。frpc 随整个项目启动、随整个项目停止，不再单独启动 Windows frpc 进程。

## 日常使用

1. Windows：双击桌面 **启动 Sub2API**。启动器会在需要时启动 Docker Desktop，启动四个服务，核对本地健康以及 frps 登录、`sub2api_ke` 注册结果。
2. Mac：使用已有的“本地 AI 控制台.app”启动已配置好的隧道，再使用 CC Switch 中已有的供应商打开 Codex。控制台需已配置对应的 Sub2API visitor；一个只配置 Ollama 的 visitor 不会自动创建 Sub2API 连接。
3. Windows 停止：Docker Desktop → Containers → `sub2api-local` 项目整行 → 停止。也可以双击原目录的 `Stop-Sub2API.cmd`。

无需重新调整两端端口。Windows 本地 CC Switch 继续访问 `http://127.0.0.1:8080/v1`，Mac 的 Sub2API 供应商继续访问 `http://127.0.0.1:18081/v1`，两端仍使用原下游 Key。原本地模型隧道沿用原配置。

Windows 需要保持开机和联网。已有 CC Switch 供应商只需首次设置，后续按已保存的供应商切换；Codex 已运行时按需重启以加载配置。

## 初次部署

已有运行目录只需使用维护方已经应用的修改，保留原数据库、`.env` 和 Compose 项目名。Git 仓库模板不是运行数据的备份。

全新 Windows 部署需要 Windows PowerShell 5.1、Docker Desktop Linux 引擎、支持 STCP 的既有 frps：

1. 在本目录双击 `Init-Sub2API.cmd`，生成本地 `.env` 和 `frp/frpc.toml`；已有文件会保留。
2. 私下填写 frps 地址、控制端口、认证 token。远端 visitor 使用相同 frps 资料、相同 STCP 密钥、`serverName="sub2api_ke"`。
3. 双击 `Start-Sub2API.cmd`。frpc 使用固定摘要的官方 0.72.0 容器镜像，首次会下载，无需安装 Windows 版 frpc。
4. 双击 `Create-DesktopShortcut.cmd`，生成 **启动 Sub2API** 桌面快捷方式。快捷方式固定指向此运行目录，不覆盖其他目录的同名入口。
5. 访问 Windows 本地管理页面，完成账号池和下游 Key 配置。管理员首次登录资料在本地 `.env` 中。

## 容器连接与配置

frpc 与 Sub2API 位于同一 Docker 网络，以 `localIP="sub2api"`、`localPort=8080` 转发。这里的 `sub2api` 是 Docker 服务名；两个容器的 `127.0.0.1` 各自指向自己，不能直接把旧 Windows 配置原样用于容器。

原 frps 地址、控制端口、认证 token、`sub2api_ke` 名称和 STCP 密钥保留。Mac visitor 配置不用因这次容器迁移修改。TLS 和 STCP 载荷加密继续开启，frpc 不发布宿主机端口；应用仍只发布 Windows `127.0.0.1:8080`。

frpc 配置只读挂载到 `/etc/frp/frpc.toml`，`log.to="console"`，日志在 Docker Desktop 的 `sub2api-frpc` 容器 Logs 中查看，日志文件有大小和数量限制。旧 `logs/frpc.log` 和 `frp/provider.pid` 仅为历史记录，当前状态以 Docker 为准。

Compose 用 `depends_on: sub2api: condition: service_healthy` 排定启动顺序，`restart: unless-stopped` 负责容器异常退出后的恢复。项目整组启停包括四个容器；单独停止一个容器与停止整个项目是不同操作，日常请操作 `sub2api-local` 项目整行。[Docker 依赖顺序说明](https://docs.docker.com/compose/how-tos/startup-order/)

## 检查与维护

- `Status-Sub2API.cmd`：显示四个容器、本地健康和脱敏 frpc 日志。
- `Stop-Sub2API.cmd`：停止四个服务，保留数据卷。
- 只停止远程 Sub2API 隧道、继续本地使用：在本目录 PowerShell 执行 `. ./Tunnel.ps1; Stop-Sub2ApiTunnel`。再次启动整个项目会恢复它。
- 不执行 `docker compose down -v`；它会删除持久卷。
- 数据仍用原 Docker 命名卷。改目录时保留原 `.env`、项目名与凭据；复制源码不能代替数据库备份。
- 未新增 Windows 启动任务或系统服务，也未改 Docker Desktop 的开机偏好。
- 本次 Docker 自身的残留套接字/保留端口故障恢复见 [MAC_HANDOFF.md](MAC_HANDOFF.md) 历史记录，未来重启后是否再现尚未验证。

## 验证与交接

Windows 已实测项目整体停止后四个容器全部退出、通过实际桌面快捷方式重新启动、Sub2API 本地健康正常、frpc 重新登录并注册。原三个容器及其卷、`.env`、本地 Codex/CC Switch 设置保留。

公网 frps 载荷转发的最新验证及 Mac 待验收项见 [MAC_HANDOFF.md](MAC_HANDOFF.md)。真实 Mac 控制台、CC Switch 和 Codex App 的结果由 Mac 分支记录，Windows 检查不代替 Mac 验收。

上游镜像与许可证见 [THIRD-PARTY.md](THIRD-PARTY.md)。所有真实凭据留在本机，仓库只有模板和脚本。

初始化和启停保留部署目录专属互斥锁；新建凭据在创建时使用仅当前用户与 SYSTEM 的 ACL。状态命令不输出原始私密日志。运行中的部署与数据未因源码合并被迁移或重启。
