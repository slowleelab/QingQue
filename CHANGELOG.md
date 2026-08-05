# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

## [Unreleased]

### Changed
- 修正文档中 LangGraph/LangChain 的错误声称，改为如实描述"asyncio + 规则路由"
- 坐席辅助引擎收敛为单一编排路径（删除旧 AssistOrchestrator 双轨）
- E3 风控独立于 ai_executor 可用（LLM 缺失时合规底线不绕过）
- detect_scene 融合 intent 入参，增加否定语义检测
- 审计中间件改用路由元数据（endpoint 函数名）推断操作类型

### Renamed
- **子工程命名收敛**（跨 3 个 commit）
  - `20c8779` — Java 端 `star-connection/` → `chat-svc/`，groupId `com.example` → `com.lumio.chatsvc`，artifactId 全部 `chat-*`，包路径 `com.example.*` → `com.lumio.chatsvc.*`
  - `5800235` — Python 端 `knowledge-platform/` → `kb-service/`，包 `app/` → `kb/`，配置前缀 `KP_` → `KB_`，ES 索引 `kp_*` → `kb_*`，Kafka 主题 `kp.ingest.*` → `kb.ingest.*`
  - (本 commit) — 主仓 `star_client.py` → `chat_client.py` + 70+ 跨仓引用；env `LUMIO_STAR_CONNECTION_URL` → `LUMIO_CHAT_SVC_URL`；类 `StarConnectionClient` → `ChatSvcClient`

### Fixed
- 删除 save_push_tracker 孤儿函数（OE 改名遗留 + Redis key 前缀不一致）

## [Unreleased] - 2026-08-04

第二轮架构师审核修复（19 项：3 P0 + 9 P1 + 7 P2）。

### Fixed (P0 — 必修)
- **P0-1** `SessionState` 缺 `*_updated_at` 字段 → `customer_memory.AttributeError`。`shared/models.py` 加 `vip_level_updated_at` / `risk_tolerance_updated_at` / `card_types_updated_at`（默认 0.0 → 触发 999 天 → 强制降级）
- **P0-2** `ws_router.cancel_event` 重赋值 `Event()` 引发 race（旧 event 仍被 `stream_chat` 闭包持有）。改用 `event.set()` + `event.clear()` 保持同一对象引用
- **P0-3** `streaming.py` 在 `CancelledError` 仍 yield fake 事件（浪费 token + flush 假消息）。移除假 yield，直接 `raise`，让 FastAPI 终止 generator

### Fixed (P1 — 高优)
- **P1-1** 4 处 `asyncio.create_task` 无引用（`bot_agent` / `decision_log` / `budget` / `tool_robustness`）。加 `_pending_tasks: set[asyncio.Task]` + `add_done_callback(discard)`，防 GC
- **P1-2** 模板缓存无锁 + I/O 阻塞。`prompts/renderer.py` 加 `asyncio.Lock` + `warmup()` 启动预热
- **P1-3** `prompt_registry` 单例 race。改用 `@functools.cache`
- **P1-4** `injection_guard.logger.warning` 记原始 `text[:100]` 泄露 PII（卡号/身份证）。改用 `mask_pii(text[:100])`
- **P1-5** `injection_guard` sanitize 替换文本含 pattern 名（信息泄露）。3 处统一改为 `"[已净化]"`
- **P1-6** `tool_robustness.ToolQuotaGuard` 配额 `incr` + 条件 `expire` 非原子（崩溃导致 key 永不过期）。改用 Lua 脚本 `INCR + EXPIRE` 原子执行
- **P1-7** 缺 `DecisionLog` 表 + alembic 迁移。新增 `c7d8e9f0a1b2_add_decision_log_table.py` + `shared/orm_models.DecisionLog` + `decision_log._write_pg()` 落库 + GDPR 删除路径覆盖
- **P1-8** `bot_agent._estimate_tokens` 与 `token_utils.estimate_tokens` 重复实现且系数不一致（0.55/0.3/0.8 vs 0.5/0.3/0.75）。删除本地实现，统一委托 `token_utils`（保留 `base_overhead=4` 的 +4 消息格式开销）
- **P1-9** `gdpr` Redis scan pattern 缺 `lumio:` 前缀。2 处 pattern 修正为 `lumio:session:*customer:{id}*`

### Fixed (P2 — 中优)
- **P2-1** `entity_sandbox.DEFAULT_ENTITY_ALLOWLIST`（7 项）与 `config.entity_pool_allowlist`（5 项）双源不一致。config 同步到 7 项
- **P2-2** `alerting.build_alert_rules` 启动时 `asyncio.create_task(_loop())` 无引用。加 `_alert_loop_tasks: set[asyncio.Task]` 防 GC
- **P2-3** `ws_router` 异常时 `str(exc)[:200]` 直接发给客户端（泄露 SQL/文件路径/库版本）。改返回 `trace_id` + 通用文案 "服务暂时不可用, 请稍后重试"
- **P2-4** `few_shot.get_cot_trigger` 接受凭空捏造的字符串（`"BILL_INSTALLMENT"` 等，与 `IntentLabel` 不匹配）。新增 `CoTIntent` enum + 别名兼容层
- **P2-5** `customer_memory` 90 天 SQL 聚合不限长（并入 P0-1 + 加 `LIMIT 1000` 防内存爆炸）
- **P2-6** `test_reflector` 缺失 + 仅 mock 假路径。新增 `tests/test_reflector.py` 8 个真实失败路径用例（client 不可用 / HTTP 异常 / JSON 解析失败 / 非法 enum / 3 次升级 ESCALATE / reset / LRU 淘汰 / singleton）

### Added
- `agent/alembic/versions/c7d8e9f0a1b2_add_decision_log_table.py` — `decision_log` 表（`session_id` / `turn_id` / `customer_id` / `agent_name` / `action` / `reasoning` / `evidence_json` / `latency_ms` / `created_at`，含 3 个复合索引支持 GDPR 删除 + 监管审计）
- `agent/lumio/shared/orm_models.py` — 新增 `DecisionLog` 模型
- `agent/lumio/services/common/decision_log.py` — 新增 `_write_pg()` 后台落库 task（持有引用防 GC，失败软处理降级 Redis-only）
- `agent/lumio/services/common/database.py` — 新增 `init_global_session_factory()` / `get_async_session_factory()` / `close_global_session_factory()`（供非 FastAPI 上下文的后台任务使用）
- `agent/lumio/main.py` — `_safe_init_global_factory()` 启动时挂入 `_COMMON_INIT_STEPS`，PG 不可用时降级不抛
- `agent/lumio/services/bot/prompts/few_shot.py` — 新增 `CoTIntent` 枚举 + `_COT_ALIASES` 别名映射
- `agent/tests/test_reflector.py` — 8 个真实失败路径单元测试

### Changed
- `bot_agent._estimate_tokens` 现为薄包装 `token_utils.estimate_tokens(text, base_overhead=4)`，保留 +4 消息格式开销语义
- `gdpr.GDPRService` 硬删除路径覆盖 `decision_log` 表（与 `dialogue_log` 一起在 `DELETE /api/customer/{id}/data` 中一并清理）

### Tests
- 单元测试：**713 passed / 0 failed / 5 skipped**（`test_reflector` 新增 8 个用例；`test_bot_memory` 因 token_utils 统一后 +4 行为差异已修复）
- 集成测试（18 个）需 Docker 中间件启动，CI 中跑；本地跳过（与本 PR 无关）

## [0.1.0] - 2026-07-28

首次公开发布。

### Added
- **Bot 自助服务**：RAG 混合检索（BM25 + 向量 + RRF 融合）、意图分类（规则快路 + LLM 慢路）、多轮对话记忆、槽位追踪、转人工
- **坐席辅助引擎**：D1/D2/D3 评估器 + E1/E2/E3 执行器并行编排（asyncio.gather + PydanticAI）、展示决策（场景+时间+反馈驱动）、仲裁融合、PII 脱敏
- **知识库管理**：文档上传/分块/双写（ES + Milvus）、FAQ CRUD + 审批工作流、语义去重
- **合规安全**：敏感词过滤、PII 脱敏、审计日志（中间件自动记录）、JWT 认证、请求限流
- **可观测性**：Prometheus 指标、Grafana 仪表盘、JSON 结构化日志（含 PII 脱敏过滤器）
- **运维后台**：Vue 3 + Element Plus 管理界面（文档管理、FAQ 管理、接入监控）
- **一键 Demo**：`make demo` 启动完整栈（含 Ollama 本地大模型降级）
- **压测脚本**：Locust 负载测试（chat send→poll 完整链路）
- **gRPC 服务端**：Classification / Retrieval / SafetyFilter 三个 proto 定义服务的 Python 实现

### Infrastructure
- Docker Compose 编排 16 个中间件（PostgreSQL / Redis / ES+IK / Milvus / Kafka / MinIO / Prometheus / Grafana）
- Alembic 数据库迁移
- GitHub Actions CI（lint / unit tests / Docker build / E2E smoke）
- Apache 2.0 License

[Unreleased]: https://github.com/slowleelab/lumio/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/slowleelab/lumio/releases/tag/v0.1.0
