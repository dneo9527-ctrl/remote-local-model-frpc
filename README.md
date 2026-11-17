# remote-local-model-frpc

远程本地模型部署与 frp：Windows 服务端与 Mac 接收端，独立分支协作开发。

当前 `windows` 分支提供两部分 Windows 源码：

- [原“本地模型控制台 / VPS 切换”面板](windows/local-model-console/README.md)：C# / WPF 完整界面、PowerShell 后端、国旗资源、构建/检查脚本和无秘密配置示例。Windows 原生运行；Mac 可编辑并据此整合共享界面，见 [Mac 交接](windows/local-model-console/MAC_HANDOFF.md)。
- **Windows 本地 Sub2API + Docker Compose + frp STCP 服务端**：Sub2API 通过上游账号池提供 API；部署与管理脚本使用上游 Docker 镜像。Windows 端只监听 `127.0.0.1:8080`，通过已有公网 frps 提供 STCP 转发。

Windows 端入口：[windows/sub2api-local/README.md](windows/sub2api-local/README.md)。包含配置模板、初始化、frpc 下载，以及启动、停止、状态检查脚本。

此分支只包含 Windows 项目。实际 `.env`、frps token、STCP 密钥、管理员密码、OAuth 账号、下游 API Key、数据库和运行日志均留在部署电脑，Git 仓库不承担凭据或数据迁移。

在 Mac 上合并 Windows 分支：

```bash
git fetch origin
# 先提交或暂存自己的修改，再切到你要接收 Windows 代码的分支：
git merge origin/windows
```

第一次拉取：

```bash
git clone --branch windows https://github.com/dneo9527-ctrl/remote-local-model-frpc.git
```

客户端连接约定与当前验证范围见 Windows 端说明。Mac 客户端代码由 Mac 侧继续合并与维护。
