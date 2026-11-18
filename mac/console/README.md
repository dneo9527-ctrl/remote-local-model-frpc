# Mac 模型接收端

核心界面：Windows 模型连接、VPS 线路、连接开关、Cherry Studio、流式网页聊天。已配置 Sub2API 时另有独立连接开关、连接选择、手动健康检查和 CC Switch 入口。更新与日志收在管理入口。

Sub2API 状态刷新只被动读取本地监听，不请求远端。只有点击“检查 API”才检查健康，不调用模型；健康通过也不代表认证、Responses 或 Codex 对话已验收。18081 被外部 visitor 占用时不接管、不停止。控制台仅管理连接，旧 Codex 配置写入动作已禁用，供应商与认证交给 CC Switch。示例设置 `automatic_health_checks=false`，连原 Ollama 健康探测也暂缓；已有配置省略该项时保留原 Ollama 检查行为。

从统一项目根目录运行 `python3 tools/launch.py --config /absolute/private/settings.json --state /absolute/private/updates`。Python 3.11+，仅标准库；复用已有网关和 frpc，不下载模型。

Mac 原生菜单栏入口源码在 `mac/menu/LocalAIConsole.swift`，构建器为 `tools/build_mac_app.py`，需要本机已有 Swift/AppKit。菜单用电脑小图标显示控制台后台状态，支持打开页面、后台停止后重新启动、正常退出；关闭网页不等于退出。菜单只检查本机 session 身份，不探测上游。退出先等待所属后台清理，独立 Ollama 连接不受影响；菜单进程意外退出时，其直接管理的后台检测父进程消失后也会退出。构建器仅输出新 `.app` 目录，不覆盖安装或添加登录项。准确本机回退材料由工具台账记录。

复制 `config.example.json` 到源码外，填写原模型管理器、解释器和私有 visitor 的绝对路径。线路列表 `ollama_routes` 每项引用自己的本机 STCP 配置；网页只收到名称与 ID，不收到密钥。切换先备份、只暂停本项目管理器；失败尝试恢复原配置。暂停状态下换线路仍保持暂停。

Windows 必须注册相同 VPS/provider/secret；本机保存了线路不等于远端已注册。网页聊天仅当前页内存历史12条、流式文字和模型选择，没有附件、持久聊天、自动压缩或MCP。

详细步骤见根目录 docs。运行测试：`python3 -m unittest discover -s tests -v`。统一打包使用根目录 `tools/package_source.py`；不要打包安装目录、私人配置或交付 ZIP。
