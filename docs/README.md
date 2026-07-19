# SmartCS 文档中心

> 技术文档入口与索引。

## 📖 核心文档

| 文档 | 说明 |
|------|------|
| [架构](./architecture.md) | 系统架构、三层设计、核心数据流、设计决策 |
| [API 参考](./api-reference.md) | Bot / Assist / 认证管理的 REST 与 WebSocket 接口 |
| [部署指南](./deployment.md) | Docker Compose 中间件、初始化、端口、监控 |
| [配置参考](./configuration.md) | 全部环境变量说明 |
| [开发指南](./development.md) | 本地开发、代码规范、测试、工作流 |

## 🧩 子项目文档

| 项目 | 文档 |
|------|------|
| `knowledge-platform/` | [README](../knowledge-platform/README.md) — 知识数据微服务 |
| `star-connection/` | [README](../star-connection/README.md) · [DESIGN](../star-connection/DESIGN.md) — 在线客服接入 |
| `web/` | [README](../web/README.md) — 前端工作台 |

## 📐 设计文档（Sprint 历史）

`docs/superpowers/specs/` 收录各迭代的完整设计：

| 文档 | 主题 |
|------|------|
| [Sprint 3 设计](./superpowers/specs/2026-05-01-sprint3-agent-orchestration-design.md) | Agent 编排 + Bot 对话 MVP |
| [Sprint 4 设计](./superpowers/specs/2026-05-03-sprint4-degradation-design.md) | LLM 集成 + 系统化降级策略 |
| [star-connection 集成设计](./superpowers/specs/2026-05-04-star-connection-integration-design.md) | 在线客服接入方案 |

规划文档见 [`docs/superpowers/plans/`](./superpowers/plans/)。

## 📊 交互式文档

- [会话状态机](./session-state-machine.html) — 3 阶段 × 7 子状态可视化
- [用户故事与流程](./user-stories-and-flows.md)（[HTML 版](./user-stories-and-flows.html)）
- [架构总览（HTML）](../agent/docs/architecture.html)

## 🤝 贡献

- [贡献指南](../CONTRIBUTING.md)
- [行为准则](../CODE_OF_CONDUCT.md)
- [安全策略](../SECURITY.md)
