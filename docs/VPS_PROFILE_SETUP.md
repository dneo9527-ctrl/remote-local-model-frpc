# 新 VPS 配套参数

同一台 Windows 可以通过多个 VPS 中转。只更换 IP 的前提是：每个新 VPS 都运行配套 frps，控制端口和认证约定一致，Windows 的对应 provider 已连接到该 VPS。Mac 换 IP 不能代替 Windows 注册。

## 固定约定

| 参数 | 值 / 含义 |
| --- | --- |
| frp 版本 | 与现用链路一致，`0.72.0` |
| VPS 控制端口 | TCP `7000` |
| frps 认证 | token；实际值在本机私有交付资料中，不进 Git |
| TLS | frps `transport.tls.force=true`，frpc `transport.tls.enable=true` |
| STCP 加密 | provider 与 visitor 均 `transport.useEncryption=true` |
| Ollama provider | `ollama`；Windows 目标 `127.0.0.1:11434` |
| Sub2API provider | `sub2api_ke`；Docker 内目标 `sub2api:8080` |
| Mac Ollama visitor | `127.0.0.1:11436`；原模型 API 网关保持 `127.0.0.1:11435` |
| Mac Sub2API visitor | `127.0.0.1:18081`；客户端地址保持 `http://127.0.0.1:18081/v1` |
| STCP secretKey | 按服务分别配对；是隧道凭据，与 Sub2API 下游 API Key 不同 |

frps 最小配置如下，须在 VPS 本机填入配套 token。无需配置公网模型端口或仪表盘。

```toml
bindAddr = "0.0.0.0"
bindPort = 7000
auth.method = "token"
auth.token = "REPLACE_WITH_PRIVATE_MATCHING_TOKEN"
transport.tls.force = true
log.to = "console"
log.level = "info"
```

这些字段以 [frp 0.72.0 frps 配置](https://github.com/fatedier/frp/blob/v0.72.0/conf/frps_full_example.toml)和 [frpc 配置](https://github.com/fatedier/frp/blob/v0.72.0/conf/frpc_full_example.toml)为准。模板开启 TLS；没有另行配置 CA 验证，不能称为自有 CA 或双向证书认证。

## 新增一个 VPS

Mac 是接收端，只运行 `frpc` visitor。下面的 `frps verify` 和服务启动操作在目标 VPS 上执行，不在 Mac 上执行。若 Mac 已因校验下载的 `frps` 出现“Apple 无法验证”弹窗，点“完成”关闭；停止再次调用该二进制，不改系统安全设置。日常控制台不调用 `frps`。

1. VPS：安装对应 CPU 架构的官方 frps `0.72.0`，私下复制配套 `frps.toml`，先执行 `frps verify -c /etc/frp/frps.toml`。以现有服务管理方式运行，安全组和系统防火墙允许 TCP 7000；8080、11434 和 Mac 回环端口无需在 VPS 开放。
2. Windows：在原部署下新增该 VPS 的完整线路副本，两个 provider 按需分别连接新 VPS。Ollama provider 使用 Windows 回环目标；Docker Sub2API provider 使用容器名 `sub2api:8080`，不能换成容器内 `127.0.0.1:8080`。不要覆盖当前好用线路或重建账号池/数据卷。
3. Mac：对应卡片 → **管理线路** → 复制原方案或导入新 VPS 的配套 visitor → 改名称与 `frps 地址`。约定相同时只改新 VPS IP；控制端口、provider、token、secret 等高级参数仍能编辑，原凭据留空保留。
4. 保存后回到首页，明确点 **应用线路 / 应用连接**。Ollama 原有开关状态按原切线事务恢复；Sub2API 应用方案后按需开启连接。两边选择相同 VPS，再逐项检查健康和真实模型请求。

本机私有交付包提供 frps、两个 Windows provider、两个 Mac visitor 的完整配套副本。它复用现有认证约定且没有下游 API Key；其中示例 `192.0.2.10` 必须替换为新 VPS IP。不要上传到 GitHub 或贴到聊天。公开源码只保留本说明，不含真实参数文件。

同一服务切到另一 VPS 后，Windows 也须完成对应切换；若希望多个 VPS 同时作为备用，Windows 需要分别运行受管理的 provider，不能假定一个 frpc 同时连接所有服务器。本版未代操作 VPS 或 Windows，多 VPS 真实切换尚未验收。
