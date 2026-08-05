# 投稿材料（Awesome 列表 / 周刊 / 社区）

> 用于向各类 awesome 列表、周刊、社区投稿的"电梯陈述"素材。不同列表格式要求不同，按需取用。
> 项目名：**灵智（Lumio）** —— 仓库地址 `slowleelab/lumio` 不变（青雀是组织/社区名）。

## 一句话简介（中文）

灵智（Lumio）— 银行级私有化智能客服参考实现：FastAPI + asyncio，RAG 检索增强 + AI 坐席辅助 + 合规过滤 + 熔断降级 + Java MCP 工具层，全链路可私有化部署，`make demo` 一键体验。

## 一句话简介（English）

Lumio — A production-grade, self-hostable intelligent customer-service platform for banking: RAG-augmented chatbot + real-time agent-assist, with compliance filtering, circuit-breaker degradation chains, 22 Java MCP credit-card tools, and full observability. FastAPI + asyncio, one-command demo via `make demo`.

## GitHub 短描述（用于 repo About / 列表条目）

```
银行级私有化智能客服参考实现 · RAG + Agent 编排 + AI 坐席辅助 + 22 MCP 工具 · 合规过滤/熔断降级/全链路监控 · FastAPI + asyncio · make demo 一键体验
```

## 英文短描述

```
Self-hostable banking-grade intelligent customer service — RAG chatbot + real-time agent assist + 22 MCP tools, compliance filter, degradation chains, full observability. FastAPI + asyncio.
```

## 投稿目标清单

| 目标 | 类型 | 投稿方式 | 状态 |
|------|------|----------|------|
| [awesome-chatgpt](https://github.com/snicco/awesome-chatgpt) / 相关 awesome-llm | GitHub 列表 | 提 PR，按格式加一行 | ⬜ |
| [awesome-fastapi](https://github.com/mjhea0/awesome-fastapi) | GitHub 列表 | 提 PR | ⬜ |
| [awesome-spring-ai](https://github.com/spring-ai-community/awesome-spring-ai) | GitHub 列表 | 提 PR（22 MCP 工具亮点） | ⬜ |
| [awesome-mcp-servers](https://github.com/modelcontextprotocol/servers) | GitHub 列表 | 提 PR（mcp-server/） | ⬜ |
| HelloGitHub 月刊 | 中文开源推荐 | 仓库 issue 自荐（用 `outreach/hellogithub-submission.md` 模板） | ⬜ |
| 掘金 / InfoQ / 思否 | 中文技术社区 | 发博客（用 `introducing-lumio.md`） | ⬜ |
| Reddit r/LocalLLaMA / r/selfhosted | 英文社区 | 发帖（用英文简介） | ⬜ |
| Product Hunt | 产品发布 | 发布（需准备截图/tagline） | ⬜ |

## 标准 awesome 条目格式（Markdown）

```markdown
- [Lumio](https://github.com/slowleelab/lumio) - Self-hostable banking-grade intelligent customer service: RAG chatbot + real-time agent assist + 22 MCP credit-card tools, with compliance filtering and degradation chains. FastAPI + asyncio.
```

中文版：

```markdown
- [灵智（Lumio）](https://github.com/slowleelab/lumio) - 银行级私有化智能客服参考实现：RAG 机器人问答 + 实时坐席辅助 + 22 个 MCP 信用卡工具，含合规过滤与熔断降级链。FastAPI + asyncio，`make demo` 一键体验。
```

## 标签 / Topics（已在仓库设置，供投稿引用）

`customer-service` · `rag` · `fastapi` · `chatbot` · `llm` · `agent-assist` · `self-hosted` · `banking` · `elasticsearch` · `milvus` · `mcp` · `spring-ai` · `higress` · `nacos`

## 投稿注意事项

- 投 awesome 列表前**先读该列表的 CONTRIBUTING.md**，多数要求：star 数门槛、描述以动词/名词开头、结尾句号、按字母序插入。
- 部分列表要求项目"存在 ≥ 30 天"或"近期有维护"，投稿前确认。
- 强调灵智（Lumio）亮点时可突出：**22 个 Java MCP 工具 + Higress AI 网关 + 4 级熔断 + 716 条 pytest 通过**。
- 深度内容引用 `introducing-lumio.md`（工程化设计说明：上下文分层/消息幂等/安全合规/可观测性）。
- Product Hunt 发布需另备：logo、3+ 张截图、60 字 tagline、首条 maker comment。
