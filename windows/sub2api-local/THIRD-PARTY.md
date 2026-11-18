# 上游来源

本目录是 Windows 部署脚本，不是 Sub2API 应用源码的完整副本。

| 组件 | 来源与许可证 | 本项目使用方式 |
| --- | --- | --- |
| Sub2API | [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)，[LGPL v3](https://github.com/Wei-Shaw/sub2api/blob/main/LICENSE) | `compose.yaml` 由上游 `deploy/docker-compose.local.yml` 调整；应用镜像固定为 `weishaw/sub2api@sha256:16046fdc6331b4f911e5417618f659b01322237f7fd19e99e0c9f4d9c54b31b5` |
| frp | [fatedier/frp](https://github.com/fatedier/frp)，[Apache 2.0](https://github.com/fatedier/frp/blob/dev/LICENSE) | Compose 使用[官方 GHCR 镜像](https://github.com/fatedier/frp/pkgs/container/frpc/versions?filters%5Bversion_type%5D=tagged) `ghcr.io/fatedier/frpc:v0.72.0@sha256:c447f0c3572a02bd3da5677f249db3b62cb02d7367d1d5bb420678ce092d65c8`；原 Windows 下载器仅保留作旧版资料，日常启动不调用 |
| PostgreSQL | [Docker 官方镜像](https://hub.docker.com/_/postgres) | `postgres:18-alpine`，上游镜像包含相应许可证 |
| Redis | [Docker 官方镜像](https://hub.docker.com/_/redis) | `redis:8-alpine`，上游镜像包含相应许可证 |

Compose 调整：数据改用命名卷、应用绑定回环地址、默认 simple 模式、固定已验证的 Sub2API 镜像摘要、改正与本项目不符的原目录迁移说明。上游许可证正文保留于 `licenses/sub2api-LICENSE.txt`。
