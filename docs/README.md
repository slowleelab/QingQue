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
| `kb-service/` | [README](../kb-service/README.md) — 知识数据微服务 |
| `chat-svc/` | [README](../chat-svc/README.md) · [DESIGN](../chat-svc/DESIGN.md) — 在线客服接入 |
| `web/` | [README](../web/README.md) — 前端工作台 |
| `mcp-server/` | Java Spring AI MCP Server（22 个信用卡工具，mock 数据） |

## 技术深度剖析（电子书）

> 全栈技术剖析：架构 / 配置 / Agent / RAG / 会话状态机 / 安全合规 / 部署 / 测试，共 21 章 + 2 附录。

| 文档 | 说明 |
|------|------|
| [电子书目录](./book/README.md) | 阅读路径与全部章节索引 |
| [序言](./book/00-preface.md) | 项目介绍与写作约定 |

## 贡献

- [贡献指南](../CONTRIBUTING.md)
- [行为准则](../CODE_OF_CONDUCT.md)
- [安全策略](../SECURITY.md)
