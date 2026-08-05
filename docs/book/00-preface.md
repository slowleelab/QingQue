---
title: "序言: 项目缘起与写作约定"
chapter: 0
part: "序言"
difficulty: "通识"
reading_time: "5 分钟"
last_updated: "2026-08-05"
summary: "Lumio 平台从 SmartCS 到 Lumio 的重命名轨迹, 4 条阅读路径, 文档写作约定."
tags: ["preface", "lumio", "smartcs", "历史"]
---

# 序言

> 灵智 (Lumio) — 银行信用卡场景的智能客服平台, 用 Python 3.11 + FastAPI + PydanticAI 串起两个独立服务: Bot 自助问答 (:8000) 与坐席辅助 (:8001), 后接 PostgreSQL / Redis / Elasticsearch / Milvus / MinIO / Kafka 六大数据基础设施.

## 项目缘起

Lumio 的前身是 **SmartCS**, 起始于 2025 年. 2026 年 6 月 (commit `4be1d67`) 正式更名为 Lumio — 名字源自拉丁语 *lumen* (光), 寓意"点亮客服对话". 重命名是技术性的: Python 包从 `smartcs/` 改为 `lumio/`, import 路径全面更新, 但业务逻辑完全保留.

`grep -r smartcs agent/lumio/` 在主代码库中已**零结果**, 重命名彻底完成.

## 项目定位

Lumio 解决的是**银行信用卡客服**这一垂直场景:

- **合规要求极高**: 文档必须经过 DRAFT → IN_REVIEW → APPROVED → PUBLISHED → SUPERSEDED/REJECTED/ARCHIVED 的 7 态审批流, 任何客户可见的知识都有 `allowed_roles` + `regulatory_tags` 双重门禁.
- **不能拒客**: 银行客服高峰期瞬时涌入, Semaphore 满载时不能返 503, 必须走 `_FAST_REPLIES` 紧急话术模板.
- **可解释性**: 坐席辅助的每个推送必须能解释「为什么推」, 仲裁器输出 `fusion_type=BLOCK|WARN|PASS` 三个明确等级, 不能黑盒.
- **审计留痕**: 5-7 年的客户对话与坐席操作必须持久化, 任何 API 调用都进 `audit_log` 表.

## Sprint 演进一览

| Sprint | 主题 | 关键成果 |
|---|---|---|
| 1 | 基础设施 + 骨架 | 12+ SubSettings 配置 / 两个 FastAPI 工厂 / Docker 中间件 |
| 2 | RAG 核心 + 知识库 | BM25 + 向量 + RRF 融合 / 5 阶段摄入 / 父-子分块 |
| 3 | Agent 编排 + Bot MVP | Redis Stream + Consumer Group / `LumioAgent` 决策树 / 工具调用 |
| 4 | LLM 集成 + 降级策略 | 4 级降级 (NORMAL/DEGRADED/FALLBACK) / 熔断器 / 健康监控 |
| 5 | Assist 引擎 | asyncio.gather 替代 Temporal / D1/D2/D3 + E1/E2/E3 / 仲裁器 |

> 详细 Sprint 设计与决策档案见 [附录 B](appendix/B-sprint-timeline.md).

## 技术债批次

2026-08-04 启动了代号 P0-P3 的技术债整改:

- **P0 (8 commit)**: 安全基线 — JWT 占位密钥拦截 / dev bypass 限定 loopback / mypy 改 advisory / CI 加固
- **P1 (3 commit)**: 高杠杆项 — gRPC 死代码删除 / dashboard 补 3 panel / Temporal 全删
- **P2 (2 commit)**: 中优先级 — dashboard 5 panel 修复 / OTel service.version 回退到 pyproject
- **P3 (8 commit)**: 真 bug 修复 — RAG NameError / 健康检查脱敏 / 输入限长 / 转人工路径修复 / CAS key 集中化

> 21 个 commit 全部已推送 main, 详见 [附录 B](appendix/B-sprint-timeline.md#p0-p3-技术债批次).

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

所有结论配数字: 16 个 SubSettings / 22 个 MCP 工具 / 35 个错误码 / 367 个测试用例 / 19 张 PG 表 / 15+ Redis key / 24 个 Docker 服务 / 3 套 Grafana dashboard / 6 条告警规则.

### 5. 时间线显式化

任何设计决策都标注来源: Sprint N / commit hash / P-batch 编号. 让历史可追溯.

### 6. 错误码 / 状态名严格统一

- 错误码形式: `3004: SessionNotFoundError`
- 状态名: `BOT_ACTIVE` (大写下划线) 而非 `bot_active` (小写蛇形)
- 配置项: `LUMIO_DATABASE__HOST` (顶层 `LUMIO_`, 嵌套 `__` 分隔)

完整术语见 [附录 A 术语表](appendix/A-glossary.md).

---

## 致谢

本系列文章基于以下贡献者的工作:

- **Lumio 核心组** (3 名): 负责 21 个 P-batch commit + Sprint 1-5 设计与实现
- **Java MCP Server 组** (2 名): Spring Boot 3.4.5 + 22 工具
- **chat-svc 组** (2 名): Spring Boot 3.1.5 + Netty + ZK 服务发现
- **前端组** (2 名): Vue 3 + Vite + WebSocket 客户端

特别感谢 P0-P3 批次中严谨的安全审计, 它发现了 8 个潜在生产事故 (P0-2 JWT 占位密钥, P0-3 dev bypass 0.0.0.0 远端绕过, P3-3 RAG NameError 5% 概率崩溃, P3-7 DoS 入口未限长 等).

---

> **下一步**: 阅读 [第 1 章: 整体架构](01-architecture-overview.md), 从三层分层 + 两个 FastAPI 实例的宏观布局开始.
