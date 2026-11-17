# Mac 模型接收端

核心界面：Windows 模型连接、VPS 线路、连接开关、Cherry Studio、流式网页聊天。更新与日志收在管理入口。原 Codex/Sub2API 接入代码和私人配置保留为独立扩展，不出现在模型首页。

从统一项目根目录运行 `python3 tools/launch.py --config /absolute/private/settings.json --state /absolute/private/updates`。Python 3.11+，仅标准库；复用已有网关和 frpc，不下载模型。

复制 `config.example.json` 到源码外，填写原模型管理器、解释器和私有 visitor 的绝对路径。线路列表 `ollama_routes` 每项引用自己的本机 STCP 配置；网页只收到名称与 ID，不收到密钥。切换先备份、只暂停本项目管理器；失败尝试恢复原配置。暂停状态下换线路仍保持暂停。

Windows 必须注册相同 VPS/provider/secret；本机保存了线路不等于远端已注册。网页聊天仅当前页内存历史12条、流式文字和模型选择，没有附件、持久聊天、自动压缩或MCP。

详细步骤见根目录 docs。运行测试：`python3 -m unittest discover -s tests -v`。统一打包使用根目录 `tools/package_source.py`；不要打包安装目录、私人配置或交付 ZIP。
