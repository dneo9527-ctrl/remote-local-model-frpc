# 远程本地模型 · frpc

**Windows 运行模型，Mac 连接与聊天。** 两端共用一个仓库和版本渠道，实际节点配置与密钥各自保存在本机。

Mac 首页只保留连接开关、VPS 线路、Cherry Studio 和网页聊天；更新与日志折叠到管理入口。Codex/Sub2API 属于独立扩展，保留原配置，不占用模型连接首页。

| 组件 | 源码 | 当前范围 |
| --- | --- | --- |
| Mac 接收端 | [mac/console](mac/console) | 原生 Ollama API、流式网页聊天、复用已有网关管理器 |
| Windows 模型面板 | [windows/local-model-console](windows/local-model-console) | 原生 WPF 界面、模型/隧道状态、线路管理、日志；复用已有模型项目 |
| Windows Sub2API 扩展 | [windows/sub2api-local](windows/sub2api-local) | 独立 Docker/STCP 部署脚本，非本地模型推理服务 |
| 双端更新 | [tools](tools) | 稳定/预览查询、校验下载、源码选择与回退；不覆盖配置或重启模型 |

![Mac 核心界面](docs/screenshots/mac-core.jpg)

![Windows 原生 WPF 的 CI 离线预览，非用户电脑实拍](docs/screenshots/windows-ci-offline-preview.png)

[操作步骤](docs/操作步骤.md) · [双端维护与更新](docs/维护与更新.md) · [验收状态](docs/验收.md) · [GitHub 发布](https://github.com/dneo9527-ctrl/remote-local-model-frpc/releases)

## 运行与连接

Python 3.11+，仅标准库。复用已有 Python/frpc；Mac 不安装 Ollama、模型权重或 Docker。完整源码解压后，将示例配置复制到**源码目录外**，填写本机已安装的路径：

```sh
python3 tools/launch.py --config /absolute/private/settings.json --state /absolute/private/updates
```

Windows 使用 `config.windows.example.json` 与 `tools/Launch-Windows.ps1`，选择同一已校验版本后，在源码外构建并打开原生 WPF 面板；配置和模型留在原项目。原生 EXE 自身无需 Python。两端更新都需关闭旧面板后重新打开，步骤见维护说明。

Mac 固定入口为 `http://127.0.0.1:11435`，OpenAI 兼容入口为 `http://127.0.0.1:11435/v1`，STCP visitor 为 `127.0.0.1:11436`。Windows Ollama 使用本机 `11434`；两端 STCP 必须连接同一 VPS、同一 provider 与配套密钥。校园网继续使用 frp，不全局切换 VPN。

连接切换只对新连接生效；不重放已经开始的生成请求。Windows 停机时本地模型不可用；Sub2API 是独立服务，不能拿它的在线状态代替 Windows 推理验证。

## 版本与发布

统一版本见 [version.json](version.json)。`windows` 保留来源端提交，`codex/mac-receiver` 是本次整合候选，审核后通过 PR 进入 `main`。稳定 tag 为 `vX.Y.Z`，预览为 `vX.Y.Z-preview.N`。源码分支变化不等于已发布新版；以 Releases 的准确 tag、附件与校验结果为准。

当前是源码预览：无打包 Python/frpc、无 Apple 公证。Windows 原生构建/隔离检查与真实 Windows 机器部署分别验收；双端多 VPS 实机切换、Codex GUI 新聊天等只在对应验收通过后才标完成。Mac 控制台与新增更新代码各有 MIT 许可；Windows Sub2API 的第三方许可见其 THIRD-PARTY。Windows 模型 GUI 与国旗资源尚缺明确许可声明，发布前需核实，不将全库统称 MIT。
