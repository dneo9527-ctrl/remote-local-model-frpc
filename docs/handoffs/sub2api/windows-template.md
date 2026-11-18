# Windows Sub2API 交接模板

这是待填写模板，所有“待填写/未验证”均不代表已执行。Windows 端确认现场后，在 `windows` 分支保存为 `docs/handoffs/sub2api/windows.md` 并提交推送。

- 北京时间：待填写。
- 来源分支：`windows`。
- 实际测试代码的完整 SHA：待填写；如为修改后的代码，先提交再测试并记录准确 SHA。
- 现场部署与该 SHA 的差异：待核对。
- 本次交接提交 SHA / 链接：提交后在交付消息给出，不在模板中猜测。

## 实际修改

待填写必要修改、原因和回退方法；实际部署路径与备份位置留在 Windows 本机检查点，公开文件使用项目相对路径。

## Windows 现场验证

| 项目 | 命令或检查 | 实际结果及时间 |
| --- | --- | --- |
| Docker / Sub2API | 原部署 `Status-Sub2API.cmd` 或对应容器状态 | 未验证 |
| 本机健康 | `Invoke-RestMethod http://127.0.0.1:8080/health` | 未验证 |
| 配置归属 | 专属 frpc 使用原部署程序与配置；与其他 frpc 区分 | 未验证 |
| 登录 frps | 仅摘录时间及脱敏的成功/失败类型 | 未验证 |
| provider 注册 | `sub2api_ke` 的 `start proxy success` 或确切脱敏错误 | 未验证 |
| 持续运行 | 注册后再次检查专属 PID 与日志是否仍正常 | 未验证 |
| 原有链路 | Windows 本地 CC Switch + Codex 是否保留 | 未验证 |

进程存在不足以证明 provider 注册成功；Windows 本机健康成功不足以证明 Mac STCP 或 App 调用成功。禁止附带完整日志或实际私有配置。

## 阻塞与交给 Mac 的下一步

待填写。如果 provider 已注册，请 Mac 复用/启动 visitor 并独立验证健康、下游 Key、Responses 流式、CC Switch 使用中及 App 新聊天回复。任何 Mac 项目均不得在此预标通过。

## 日常维护

- 启动：待核实原部署 `Start-Sub2API.cmd` 的现场行为。
- 检查：待核实原部署 `Status-Sub2API.cmd` 的现场行为。
- 停止：先说明原 `Stop-Sub2API.cmd` 的实际影响范围，再按需要使用；不要终止其他项目的 frpc 或容器。
- 回退：待填写已备份配置的本机记录引用及准确恢复操作。
