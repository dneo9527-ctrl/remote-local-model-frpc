# Sub2API 双端工作交接

仓库：`dneo9527-ctrl/remote-local-model-frpc`。用户于 2026-11-18 指定：后续 Windows 和 Mac 在同一 GitHub 项目的不同分支完成交接。

| 端 | 工作分支 | 本端维护的记录 | 验收责任 |
| --- | --- | --- | --- |
| Windows | `windows` | `docs/handoffs/sub2api/windows.md` | Docker Sub2API、本机健康、专属 frpc 归属及 provider 注册 |
| Mac | `codex/sub2api-mac` | [mac.md](mac.md) | visitor 转发、下游 Key、Responses 流式、CC Switch、Codex App 新聊天 |

Windows 首次记录可使用 [windows-template.md](windows-template.md)。此模板由 Mac 提供，内容不代表 Windows 已执行或验收。

## 连接约定

```text
Mac Codex App
  → CC Switch 保存的 Windows Sub2API 供应商
  → http://127.0.0.1:18081/v1（Mac frp STCP visitor）
  → 现有 frps / provider sub2api_ke
  → http://127.0.0.1:8080（Windows Docker Sub2API）
  → 上游账号池
```

- 原生 OpenAI Responses；优先实测 `gpt-6.1-sol`，不可用时依据网关实际返回的模型选择。
- Windows `sub2api_ke` 为 STCP provider，仅转发 `127.0.0.1:8080`；Mac visitor 仅监听 `127.0.0.1:18081`。
- 两端在本机核对同一 frps、认证及配套 STCP secret；下游 API Key 单独读取，不与隧道认证混用。
- CC Switch 的本地路由端口与 18081 分开。直连模式下只管理供应商配置，客户端直接请求 visitor；路径仅包含一次 `/v1`。

## 每次交接的最少内容

1. 北京时间、来源分支、**实际测试代码的完整提交 SHA**。部署副本与仓库不一致时列出差异，不能用仓库 HEAD 代替现场版本。
2. 修改项及范围、脱敏验证步骤和实际结果，区分本机现场、CI、静态阅读和历史记录。
3. 当前阻塞、未执行项、对端下一步；启动、停止及回退办法。实际私有部署路径和备份位置留在各端本机检查点。
4. 提交并推送本端工作分支，在本端交付消息给出**交接提交的完整 SHA 和 GitHub 链接**。记录自身可用 `git log -1 --format=%H -- docs/handoffs/sub2api/` 查找，不在文件中虚构自身提交号。

各端只维护自己的现场结果。Mac 不预填 Windows 的健康或注册成功，Windows 不预填 Mac 的 App 调用成功。旧记录保留日期，不能当作当前在线证明。

## 接收对端交接

先检查本地工作区和未提交文件，再抓取并只读查看对端分支：

```sh
git status --short --branch
git fetch origin
# Windows 接收 Mac：
git log -1 --format=%H origin/codex/sub2api-mac
git show origin/codex/sub2api-mac:docs/handoffs/sub2api/mac.md
git show origin/codex/sub2api-mac:docs/handoffs/sub2api/windows-template.md
# Mac 接收 Windows（Windows 端首次提交后才存在）：
git log -1 --format=%H origin/windows
git show origin/windows:docs/handoffs/sub2api/windows.md
```

接着按对方交付的**准确提交 SHA**阅读交接和代码 diff，独立核验本端能测的部分。示例使用分支名用于定位，确认后以该次完整 SHA 为证据。

如需接收代码，只选择本任务所需且已审查的提交，通过本端分支合并或 cherry-pick。不要盲目合并整条 Windows 历史，不覆盖未提交工作，不强推对方分支。`windows/local-model-console/MAC_HANDOFF.md` 面向模型面板整合，不替代本目录的 Sub2API 交接。

分支交接不等于发布或部署；合入 `main`、发布版本和应用到现场分别处理。双方在任务活跃时抓取和处理；本约定不创建后台监视、定时任务，也不保证唤醒另一端 Codex。

## 独立验收顺序

1. Windows：`/health` 返回 `status=ok`；专属 frpc 正在运行且配置归属正确；`sub2api_ke` 注册成功。进程存在不足以证明注册成功。
2. Mac：复用或启动正确 visitor，`http://127.0.0.1:18081/health` 成功。visitor 登录 frps 不足以证明实际转发成功。
3. Mac：有 Key 的 `/v1/models` 成功；请求实际支持模型的 `/v1/responses`，观察流式输出和完成事件。
4. Mac：CC Switch 中供应商为“使用中”；有效模型、base URL、provider 标识和认证一致，公共配置及原登录条目保留。
5. Mac：正常退出并重新打开 Codex App，新建聊天发简短请求，观察真实回复。模型列表、CLI 登录状态和网页访问均不能替代此项。

## 公开仓库的内容边界

只提交必要源码、示例和脱敏事实。连接包、实际 `.env`/TOML、下游 Key、OAuth、`auth.json`、数据库、原始日志、私人机器路径和账号信息留在本机。不要提交私有值的摘要、片段或可拼接材料。只暂存本次指定文件，检查暂存 diff 后再推送；`.gitignore` 不能代替内容检查。
