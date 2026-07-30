# 灵智（Lumio）文档中心

> 银行信用卡智能客服平台 —— 技术文档入口与索引。

## 核心文档

| 文档 | 说明 |
|------|------|
| [架构](./architecture.md) | 三层架构、核心数据流、设计决策 |
| [API 参考](./api-reference.md) | Bot / Assist / 认证 / 管理 / KB 的 REST 接口 |
| [部署指南](./deployment.md) | Docker Compose / Kubernetes / AI 网关 |
| [配置参考](./configuration.md) | 全部 `LUMIO_*` / `POSTGRES_*` / `REDIS_*` / ... 环境变量 |
| [开发指南](./development.md) | 本地开发、代码规范、测试、工作流 |
| [性能基准](./benchmark.md) | 微基准、Locust 负载测试方法与历史数据 |
| [用户故事与流程](./user-stories-and-flows.md) | 客户 / 坐席 / 运营 故事与会话生命周期 |

## 子项目文档

| 项目 | 文档 |
|------|------|
| `knowledge-platform/` | [README](../knowledge-platform/README.md) — 知识数据微服务 |
| `star-connection/` | [README](../star-connection/README.md) · [DESIGN](../star-connection/DESIGN.md) — 在线客服接入 |
| `web/` | [README](../web/README.md) — 前端工作台 |
| `mcp-server/` | Java Spring AI MCP Server（22 个信用卡工具，mock 数据） |

## 设计文档（迭代历史）

`docs/superpowers/specs/` 收录各迭代的完整设计：

| 文档 | 主题 |
|------|------|
| [Sprint 3 设计](./superpowers/specs/2026-05-01-sprint3-agent-orchestration-design.md) | Agent 编排 + Bot 对话 MVP |
| [Sprint 4 设计](./superpowers/specs/2026-05-03-sprint4-degradation-design.md) | LLM 集成 + 系统化降级策略 |
| [star-connection 集成设计](./superpowers/specs/2026-05-04-star-connection-integration-design.md) | 在线客服接入方案 |
| [超级图设计](./superpowers/specs/2026-05-01-super-diagram-design.md) | 三层架构图（编排 / 能力 / 数据） |

各 Sprint 的实施计划见 [`docs/superpowers/plans/`](./superpowers/plans/)。

## 品牌重命名说明

`SmartCS` → `灵智（Lumio）` 的重命名分三个 commit 完成：

| Commit | 范围 |
|--------|------|
| `4be1d67` | Python 包 `smartcs` → `lumio`，异常基类 `SmartCSError` → `LumioError`（24 个子类同改） |
| `0908f82` | 资源 / 容器 / 镜像 / Java `mcp-server` / `star-connection` / proto / web 前端；Java groupId `com.smartcs` → `com.lumio` |
| 本 commit | 文档 / 历史 / UAT / 博客 |

详细迁移清单见 [CHANGELOG.md](../CHANGELOG.md)。

## 贡献

- [贡献指南](../CONTRIBUTING.md)
- [行为准则](../CODE_OF_CONDUCT.md)
- [安全策略](../SECURITY.md)
