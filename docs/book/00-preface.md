---
title: "序言: 项目介绍与写作约定"
chapter: 0
part: "序言"
difficulty: "通识"
reading_time: "5 分钟"
last_updated: "2026-08-05"
summary: "Lumio 平台定位, 4 条阅读路径, 文档写作约定."
tags: ["preface", "lumio", "阅读指南"]
---

# 序言

> 灵智 (Lumio) — 银行信用卡场景的智能客服平台, 用 Python 3.11 + FastAPI + asyncio 串起两个独立服务: Bot 自助问答 (:8000) 与坐席辅助 (:8001), 后接 PostgreSQL / Redis / Elasticsearch / Milvus / MinIO / Kafka 六大数据基础设施.

## 项目定位

Lumio 解决的是**银行信用卡客服**这一垂直场景:

- **合规要求极高**: 文档必须经过 DRAFT → IN_REVIEW → APPROVED → PUBLISHED → SUPERSEDED/REJECTED/ARCHIVED 的 7 态审批流, 任何客户可见的知识都有 `allowed_roles` + `regulatory_tags` 双重门禁.
- **不能拒客**: 银行客服高峰期瞬时涌入, Semaphore 满载时不能返 503, 必须走 `_FAST_REPLIES` 紧急话术模板.
- **可解释性**: 坐席辅助的每个推送必须能解释「为什么推」, 仲裁器输出 `fusion_type=BLOCK|WARN|PASS` 三个明确等级, 不能黑盒.
- **审计留痕**: 5-7 年的客户对话与坐席操作必须持久化, 任何 API 调用都进 `audit_log` 表.

## 4 条阅读路径

根据角色选择:

1. **新成员 (1-2 天)**: 01 → 03 → 05 → 12 → 06
2. **架构师 (半天)**: 00 → 01 → 02 → 04 → 05 → 11 → 13
3. **业务开发者 (按需)**: 01 → 02 → 03/04 → 08 → 10
4. **运维 (半天)**: 01 → 12 → 13 → 10 → 附录 C

详细见 [README.md 阅读路径](README.md#阅读路径).

## 写作约定

为了让这本书在 1-2 年后仍然可用, 严格遵守以下约定:

### 1. 「为什么」优先于「是什么」

每个设计决策都先讲 *为什么这么选*, 再讲 *怎么实现*. 例如 RAG 不直接讲检索算法, 而是先讲「为什么 BM25 + 向量 + RRF」的三路召回.

### 2. 代码引用而非复制

关键代码用 5-15 行片段 + 行号标注. 例如:

```python
# agent/lumio/services/bot/router.py:289
async def _session_worker(self, session_id: str) -> None:
    """per-session 串行消费, 300s 空闲自动退出"""
    ...
```

完整实现见 `agent/lumio/services/bot/router.py:289-340`. 避免文档腐烂, 也避免照抄代码.

### 3. mermaid 优先于 ASCII

时序图/状态图/流程图一律使用 mermaid 语法, 源码在 `diagrams/*.mmd`, 文档用 `\`\`\`mermaid` 代码块引用.

### 4. 数字驱动

所有结论配数字: 16 个 SubSettings / 22 个 MCP 工具 / 35 个错误码 / 740+ 个测试用例 / 19 张 PG 表 / 15+ Redis key / 24 个 Docker 服务 / 3 套 Grafana dashboard / 6 条告警规则.

### 5. 命名严格统一

- 错误码形式: `3004: SessionNotFoundError`
- 状态名: `BOT_ACTIVE` (大写下划线) 而非 `bot_active` (小写蛇形)
- 配置项: `LUMIO_DATABASE__HOST` (顶层 `LUMIO_`, 嵌套 `__` 分隔)

完整术语见 [附录 A 术语表](appendix/A-glossary.md).

---

## 致谢

本系列文章基于以下贡献者的工作:

- **Lumio 核心组** (3 名): 平台设计与实现
- **Java MCP Server 组** (2 名): Spring Boot 3.4.5 + 22 工具
- **chat-svc 组** (2 名): Spring Boot 3.1.5 + Netty + ZK 服务发现
- **前端组** (2 名): Vue 3 + Vite + WebSocket 客户端

---

> **下一步**: 阅读 [第 1 章: 整体架构](01-architecture-overview.md), 从三层分层 + 两个 FastAPI 实例的宏观布局开始.
