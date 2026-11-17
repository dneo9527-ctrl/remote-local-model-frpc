# 上游来源

本目录是 Windows 部署脚本，不是 Sub2API 应用源码的完整副本。

| 组件 | 来源与许可证 | 本项目使用方式 |
| --- | --- | --- |
| Sub2API | [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)，[LGPL v3](https://github.com/Wei-Shaw/sub2api/blob/main/LICENSE) | `compose.yaml` 由上游 `deploy/docker-compose.local.yml` 调整；应用镜像固定为 `weishaw/sub2api@sha256:16046fdc6331b4f911e5417618f659b01322237f7fd19e99e0c9f4d9c54b31b5` |
| frp | [fatedier/frp](https://github.com/fatedier/frp)，[Apache 2.0](https://github.com/fatedier/frp/blob/dev/LICENSE) | 安装脚本下载官方 `v0.72.0` Windows x64 ZIP 并验证固定摘要；二进制不存入 Git |
| PostgreSQL | [Docker 官方镜像](https://hub.docker.com/_/postgres) | `postgres:18-alpine@sha256:b6972e1115a4ff8eba5d7f7d19184926d1e16d7d1136ae1e1a7ebf286da0f015`，上游镜像包含相应许可证 |
| Redis | [Docker 官方镜像](https://hub.docker.com/_/redis) | `redis:8-alpine@sha256:7958cfbb9d152f87dadfbc89f1b0b15938f0b81ff8982feb327476f4911935c3`，上游镜像包含相应许可证 |

Compose 调整：数据改用命名卷、应用绑定回环地址、默认 simple 模式、固定已验证的 Sub2API 镜像摘要、改正与本项目不符的原目录迁移说明。上游许可证正文保留于 `licenses/sub2api-LICENSE.txt`。

PostgreSQL、Redis 摘要于 2026-11-17 从 Docker Hub 官方 `/v2/repositories/library/<name>/tags/<tag>` API 的 `digest` 字段取得（多架构 manifest index）。本次只锁定仓库候选，不自动升级已部署容器或数据卷。
