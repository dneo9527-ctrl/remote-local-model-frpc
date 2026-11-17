# Windows 本地 Sub2API + frp STCP

用 Windows 上的 Docker Desktop 运行 Sub2API、PostgreSQL 和 Redis，再用独立 frpc 进程连接已有公网 frps。Sub2API 自带上游账号池；远端客户端通过 STCP visitor 访问本地转发端口。

## 文件与依赖

- Windows 10/11 x64、Windows PowerShell 5.1、Docker Desktop（Linux containers + Compose v2）。
- 已有可用的 frps，控制端口、认证方式与本地配置一致，允许 STCP。
- `compose.yaml`：Sub2API 固定镜像摘要，PostgreSQL 18 Alpine、Redis 8 Alpine 均固定镜像摘要，使用 Docker 命名卷。
- `.env.example`、`frp/frpc.example.toml`：不含真实凭据的模板。
- `Init-Sub2API.cmd`：生成本机随机凭据；已有 `.env` 和 `frp/frpc.toml` 会保留。
- `Install-Frpc.cmd`：从 frp 官方 Release 下载 Windows x64 0.72.0，核对固定 SHA-256 后安装到 `bin/frpc.exe`。
- `Start-Sub2API.cmd`、`Stop-Sub2API.cmd`、`Status-Sub2API.cmd`：启动、停止与检查。

本目录只包含 Windows 部署代码和模板。Mac 接收端及共享控制台由仓库其他目录维护。应用源码在 [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)，不包含当前电脑的账号池、数据库或运行时二进制。

## 全新 Windows 部署

1. 安装并打开 Docker Desktop，确认 Linux 引擎就绪。
2. 完整克隆项目，在此目录双击 `Init-Sub2API.cmd`。它为数据库、Redis、管理员、JWT、TOTP 和 STCP 分别生成随机值，实际配置写入本地 `.env` 与 `frp/frpc.toml`。
3. 本地编辑 `frp/frpc.toml`：填写已有 frps 的 `serverAddr`、`serverPort`、`auth.token`。`secretKey` 是此隧道自己的密钥；远端 visitor 必须使用相同值。代理名称 `sub2api_ke` 必须在 frps 上唯一。
4. 双击 `Install-Frpc.cmd` 下载 frpc。也可手动从[官方 0.72.0 Release](https://github.com/fatedier/frp/releases/tag/v0.72.0)取得 `frp_0.72.0_windows_amd64.zip`，核对下方摘要后把 `frpc.exe` 与许可证放到 `bin`。
5. 双击 `Start-Sub2API.cmd`。它先检查配置，启动 Docker 容器，等待 `/health` 正常，再启动专属 frpc。首次启动需要下载镜像。
6. 打开 [本地管理页面](http://127.0.0.1:8080)，用 `.env` 中的 `ADMIN_EMAIL`、`ADMIN_PASSWORD` 登录。管理员仅在全新数据库首次启动时按环境配置创建；改 `.env` 不会重置已有管理员密码。
7. 在 Sub2API 后台手动授权上游账号，分配到相应 OpenAI 分组，再为远端客户端创建属于该分组的下游 API Key。

官方 frp Windows x64 0.72.0 ZIP 的 SHA-256：

```text
5197c1b69564089cdc229fc1260a0e25db1e722b1b7db45fe1542c3c43edf9ae
```

本项目固定使用 `127.0.0.1:8080`，frp `localPort` 和脚本健康检查使用相同端口。启动前会检查 Docker Compose 的**实际展开配置**，拒绝公开绑定、其他应用端口及数据库/Redis 宿主端口，包括 Shell 环境变量带来的覆盖。配置文件、`.env` 和 Compose 项目名均明确传入 Docker，不使用无关的 `COMPOSE_FILE` / `COMPOSE_PROJECT_NAME` Shell 设置。不要仅修改一个端口值；需要改端口时先统一变更并重新验收。

## 已有部署继续维护

克隆此仓库只同步代码，**不会搬迁、更新或重启现有部署**。继续从原部署目录运行其启停脚本即可。已配置的密钥和上游账号无需因上传 GitHub 重新生成。

确需更换 Windows 管理目录时，先从旧目录停止该项目；将旧 `.env`、`frp/frpc.toml` 与 `bin/frpc.exe` 私下复制到新目录，保留原 `COMPOSE_PROJECT_NAME`，并把 frpc 的 `log.to` 改为相对路径 `logs/frpc.log`。核对配置后再从新目录启动。不要让两份目录同时注册相同 STCP 代理名称。

相同 Docker 主机上保持相同 Compose 项目名和卷名，才能继续使用原数据库；已有数据库必须保留原数据库凭据、JWT/TOTP 密钥。跨主机迁移还需单独备份、恢复 Docker 卷，复制源码目录不能代替数据库备份。

## 日常使用

```powershell
# 在本目录执行，也可以双击同名 .cmd
.\Start-Sub2API.ps1 -NoBrowser
.\Status-Sub2API.ps1
.\Stop-Sub2API.ps1
```

初始化、安装、启动和停止通过此目录专属的 Windows 命名 Mutex 互斥；并发操作会明确拒绝，进程异常退出后锁会释放。启动/停止只管理此目录专属 frpc 的完整程序路径、完整 `-c` 配置参数及创建时间，保留其他项目的 frp 进程。新建凭据文件会在写入前移除继承 ACL，只允许当前 Windows 用户和 SYSTEM；已有配置和 ACL 不会被重新初始化覆盖。停止使用 `docker compose stop`，保留全部数据卷；不要执行 `docker compose down -v`，它会删除卷数据。

frpc 日志在 `logs/frpc.log`。`Status` 用来确认容器、健康接口和项目隧道进程，默认不把原始日志打印到共享终端；frps 注册是否成功应查看日志中的 `login to server success` / `start proxy success` 等记录，进程存在并不等于远程已连通。

容器设置 `restart: unless-stopped`，Docker 引擎运行时可恢复；本项目未安装 Windows 启动任务或服务。重启 Windows 后运行 `Start-Sub2API.cmd`。提供远程服务时 Windows 需保持开机、联网且不休眠。frpc 可重连网络，但脚本不是进程崩溃后的后台看守。

## 供 Mac 侧合并的连接约定

| 项目 | 约定 |
| --- | --- |
| Windows 应用 | `http://127.0.0.1:8080` |
| Windows provider | `type = "stcp"`，名称默认 `sub2api_ke` |
| 远端 visitor | `serverName` 与 provider 名一致；frps 地址/认证及 STCP `secretKey` 私下填写 |
| 建议 visitor 监听 | `127.0.0.1:18081` |
| 远端健康检查 | `http://127.0.0.1:18081/health` |
| OpenAI 兼容 API base URL | `http://127.0.0.1:18081/v1` |
| Codex 协议 | `responses`，模型用账号池实际支持的 ID |
| 客户端认证 | Sub2API 后台生成的下游 API Key |

接口路径是 `/v1`；`/openai/v1` 可能返回前端 HTML，不能用作 API base URL。健康检查成功后，还应带下游 Key 验证 `/v1/models` 与一次 `/v1/responses` 流式请求。

provider 配置启用 `transport.tls.enable=true` 和 `transport.useEncryption=true`；visitor 需配置对应的 TLS 与 STCP 加密。两端应用到本机 frpc 的回环段使用 HTTP。TLS 的服务端身份校验需结合 frps 自身证书配置；此模板不配置或替换公网 frps。

## 验证范围

原 Windows 部署已通过容器健康检查、无 Key 请求返回 401、带 Key 模型列表与 Responses 流式请求；也已用 Windows 临时 visitor 经公网 frps 完成真实上游流式请求。**这不等于 Mac 上的 Codex App 已完成验收**，Mac 合并后仍须实际测试。

本次仓库整理已在 Windows PowerShell 5.1 验证：6 个脚本解析通过；初始化生成独立随机凭据，第二次运行保留原文件；Compose 配置、回环绑定和命名卷检查通过；frpc 模板 `verify` 通过；未填写 frps 占位符时启动预检会拒绝。官方 frpc 下载、固定 SHA-256 校验、0.72.0 版本及重复安装保留原程序均已实测。另检查了 15 项 Git 忽略规则，并扫描待上传文本，未包含已知部署私密值或本机用户绝对路径。

这些检查在隔离目录执行，未从仓库副本启动容器或隧道，运行中的旧目录未被改写。历史部署验证用的凭据与结果原文不上传。

上游来源和许可证见 [THIRD-PARTY.md](THIRD-PARTY.md)。

## 本次统一整合的回归检查

运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\Verify-Windows.ps1
```

该测试在唯一临时目录实际执行初始化，检查随机凭据彼此独立、受限 ACL、重复初始化字节不变、跨进程互斥；用内存假进程/Compose 结果检查完整进程身份、停止隔离、有效回环绑定。它不启动 Docker、隧道或上游模型，不读取现有部署的密钥。GitHub 的 `Windows deployment checks` 使用原生 Windows PowerShell 5.1 运行，具体提交的 CI 结果才是本次通过凭据。

此次代码还固定了 PostgreSQL、Redis 镜像摘要；摘要读取自 Docker Hub 官方 tag API（2026-11-17），见 THIRD-PARTY.md。固定版本是候选源码的可重复部署约定，**没有在用户的 Windows 上拉取镜像、升级数据库或重启服务**。现有部署升级前仍须单独备份命名卷，检查上游兼容性。旧的部署验证与新回归测试不能互相替代。

本目录没有独立 Windows 图形面板；`.cmd` 是终端入口，Sub2API 网页由上游镜像提供。共享控制台的 Windows runner 截图也不等同于用户这台 Windows 上的服务实机验收。
