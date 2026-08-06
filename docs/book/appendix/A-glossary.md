---
title: "附录 A: 术语表"
part: "附录"
last_updated: "2026-08-05"
summary: "60+ Lumio 项目核心术语速查. 涵盖错误码段、状态机、配置项、技术概念."
tags: ["glossary", "术语"]
---

# 附录 A: 术语表

> 本术语表是本书其他章节的统一词汇表. 任何术语出现时, 含义以本表为准.

---

## A.1 业务领域术语

| 术语 | 英文 | 含义 |
|---|---|---|
| 灵智 | Lumio | 项目名, 银行信用卡智能客服平台. |
| 自助问答 | Bot Self-Service | 客户通过文本/语音直接与 AI 对话, 不经坐席. :8000. |
| 坐席辅助 | Agent Assist | 坐席与客户通话时, AI 实时推送话术/知识/合规提醒. :8001. |
| 转人工 | Transfer to Agent | Bot 无法处理时, 将会话升级到人工坐席. |
| 通话小结 | Call Summary | 通话结束后 AI 自动生成的结构化小结 (JSON). |
| 坐席工号 | Agent ID | 坐席唯一标识, WebSocket 连接绑 agent_id. |
| 会话 ID | Session ID | 一次客户-坐席/客户-Bot 对话的唯一标识 (UUID v7). |

## A.2 错误码段

> 完整异常层次见 [第 8 章 错误处理](chapters/08-error-handling.md).

| 段 | 含义 | HTTP 默认 |
|---|---|---|
| **1xxx** | 认证授权 (AuthenticationError 1001 / AuthorizationError 1003) | 401/403 |
| **2xxx** | 输入错误 (IntentUnrecognizedError 2001 / DocumentFormatError 2010) | 400 |
| **3xxx** | 业务错误 (SessionNotFoundError 3004 / InvalidTransitionError 3005) | 422 |
| **4xxx** | 外部依赖错误 (LLMTimeoutError 4001 / EmbeddingServiceError 4005) | 502 |
| **5xxx** | 系统错误 (ServiceOverloadedError 5002 / OrchestrationTimeoutError 5004) | 500/503 |

## A.3 会话状态机

> 完整状态机见 [第 6 章 会话状态机](06-session-state-machine.md).

| 名称 | 含义 |
|---|---|
| **SessionPhase** | 顶层阶段: BOT / AGENT / ENDED / legacy |
| **SessionSubPhase** | 子状态 (7 个): BOT_ACTIVE, AG_QUEUED, AG_ASSIGNED, AG_ACTIVE, AG_ON_HOLD, AG_REVIEWING, ENDED |
| **VALID_TRANSITIONS** | 状态转换白名单 (Lua CAS 校验) |
| **CAS (Compare-And-Swap)** | 原子比较并设置, Redis Lua 脚本实现 (session.py:67) |
| **pending_action** | 等待客户确认的工具调用 (5 态: pending/confirm/cancel/unclear/expired) |

## A.4 Bot Agent 术语

| 名称 | 含义 |
|---|---|
| **LumioAgent** | Bot 主入口类, 6 步决策树 (bot_agent.py:92) |
| **IntentLabel** | 意图标签: FAQ, BILL, TRANSACTION, LIMIT, INSTALLMENT, REWARD, CARD_LOSS, COMPLAINT, TRANSFER_AGENT, CHITCHAT |
| **SlotTracker** | 槽位追踪, 跨轮持久化到 Redis (`lumio:slot:{session_id}`) |
| **consumer_loop** | Redis Stream 消费者, 15s 一次指标采集 (router.py:732) |
| **session_worker** | per-session 串行消费协程 (router.py:289) |
| **dispatch_message** | 按 session_id 路由到 per-session Queue (router.py:270) |
| **claim_stale** | XAUTOCLAIM 兜底, 60s 超时 + 3 次重试转死信 (router.py:204) |
| **_FAST_REPLIES** | Semaphore 满载时紧急话术模板 (router.py:92) |
| **PEL (Pending Entries List)** | Redis Stream 已读未确认消息, XAUTOCLAIM 接管 |

## A.4.1 上下文工程术语 (第 15 章)

> 完整上下文工程见 [第 15 章 上下文工程](chapters/15-context-engineering.md).

| 名称 | 含义 |
|---|---|
| **3 层上下文 (Layer 1/2/3)** | Layer 1 结构化会话记忆 (注入 system_prompt 顶部, 永不裁剪) / Layer 2 近期对话历史 (token 预算裁剪) / Layer 3 RAG 检索上下文 (调用方传入) |
| **`_build_session_memory`** | bot_agent.py:725-779, 5 段拼接: 对话摘要/客户画像/已知实体/意图历史/当前意图 |
| **`_load_history`** | bot_agent.py:583-629, LIFO 累加 + 关键轮次豁免 + 字符类 token 估算 |
| **`_estimate_tokens`** | bot_agent.py:61-74, CJK 系数 0.55 / 拉丁 0.3 / 其他 0.8 + 4 消息格式开销 |
| **`_IMPORTANT_KEYWORDS`** | bot_agent.py:79-85, 17 个永远不被裁剪的关键词 (投诉/银保监/盗刷/挂失/转人工等) |
| **`_ensure_summary`** | bot_agent.py:631-723, 增量对话摘要, `last_summarized_turn_id` 精确追踪 |
| **`MaxContextTokens`** | LLM 上下文总预算, 默认 4096 |
| **`ReservedTokens`** | 预留回答空间, 默认 2048 |
| **`TokenBudget`** | 历史可用 token = max(MaxContext - Reserved, 1024) |
| **`LastSummarizedTurnID`** | 已摘要轮次的 ID, 用于增量摘要追踪, 防 LTRIM 漂移 |
| **会话摘要 (conversation_summary)** | 被裁剪轮次的浓缩, ≤ 500 字, 写回 SessionState |
| **fire-and-forget 摘要** | `asyncio.create_task` 异步触发, 不阻塞用户请求 |

## A.4.2 客户记忆术语 (第 16 章)

> 完整客户记忆见 [第 16 章 客户记忆与知识图谱](chapters/16-customer-memory-and-kg.md).

| 名称 | 含义 |
|---|---|
| **`learn_customer_profile`** | customer_memory.py:52-128, SQL `string_agg` 聚合 90 天历史对话 |
| **`apply_learned_profile`** | customer_memory.py:131-172, CAS patch 写入 SessionState, 不覆盖已显式声明 |
| **`_CARD_TYPE_PATTERNS`** | customer_memory.py:30-35, 4 类卡种正则 (platinum/diamond/gold/standard) |
| **`_VIP_SIGNALS`** | customer_memory.py:38-42, 3 档 VIP (private_banking=5 / wealth_management=4 / vip=3), max-score 评分 |
| **`_RISK_SIGNALS`** | customer_memory.py:45-49, 风险评分累加, R1~R4 阈值 |
| **R1~R4 风险等级** | 保守 / 中性偏保守 / 偏高 / 激进 |
| **VIP 评分机制** | max-score wins, 非累加; 默认 "普通" |
| **`LookbackDays`** | 客户画像学习窗口, 默认 90 天 |
| **`string_agg`** | PostgreSQL 聚合函数, 把多行 text 字段合并为单字符串 |
| **「不覆盖已显式声明」** | 仅当 `state.vip_level == "普通"` 或为空时才 patch |
| **VIP 默认值 = "普通"** | 中文默认值, 与 `SessionState.vip_level` 字段对齐 |
| **`writer="customer_memory:learn"`** | CAS patch 审计标签, 用于追溯学习操作 |

## A.4.3 知识图谱术语 (第 16 章)

> 完整知识图谱见 [第 16 章 客户记忆与知识图谱](chapters/16-customer-memory-and-kg.md).

| 名称 | 含义 |
|---|---|
| **`_ENTITY_GRAPH`** | knowledge_graph.py:21-47, 5 实体 × 3 关系 (信用卡/账单/额度/分期/挂失) |
| **8 种关系谓词** | has_type / has_feature / has_method / has_cycle / has_factor / has_step / has_fee / related_to |
| **`query_entity_relations`** | knowledge_graph.py:50-77, OR 命中: 实体名 in 查询文本 OR entity 参数与实体名互相包含 |
| **`enrich_retrieval_context`** | knowledge_graph.py:80-104, Markdown `## 知识图谱补充信息:` 前缀追加 |
| **空 entity 防御** | `bool(entity) and (entity in entity_name or entity_name in entity)`, 避免空串命中所有实体 |
| **Neo4j 切换路径** | `_ENTITY_GRAPH` 改为 client wrapper, `query_entity_relations` 改 Cypher, 接口签名保持稳定 |
| **仅 knowledge_agent 分支** | bot_agent.py:213-216, business/fallback/tool 分支不调 KG |

## A.4.4 工具调用术语 (第 17 章)

> 完整工具调用见 [第 17 章 工具调用与确认状态机](chapters/17-tool-calling-and-confirmation.md).

| 名称 | 含义 |
|---|---|
| **Progressive Disclosure (PD)** | tool_selection.py, 按意图+置信度裁剪工具子集, 默认关 (零回归) |
| **`progressive_disclosure_enabled`** | 开关, 默认 False, 关闭时返回 None 暴露全量 |
| **`pd_confidence_threshold`** | 置信度阈值, 默认 0.7, 低于则不裁剪 |
| **`intent_tool_map`** | 5 意图 → 17 工具映射表 (config.py:417-435) |
| **`TOOL_INTENTS`** | tool_selection.py:24-32, 5 工具意图白名单 (BILL/TRANSACTION/LIMIT/INSTALLMENT/REWARD) |
| **`select_tools_for_intent`** | tool_selection.py:35-61, 3 重零回归保险纯函数 |
| **`ToolCallingExecutor`** | tool_executor.py:104-216, LLM 工具循环 + 确认状态机 |
| **`_run_loop`** | tool_executor.py:220-280, 4 分支: 无 tool_call / 护栏拒绝 / 敏感 pending / 非敏感执行 |
| **`max_tool_iterations`** | 工具循环上限, 默认 5, 超限抛 RuntimeError |
| **`detect_confirmation`** | tool_executor.py:75-87, cancel 关键词优先于 confirm |
| **`_CANCEL_KEYWORDS`** | 13 个: 取消/不用/不要/不办/不确认/不同意/不可以/算了/放弃/别/停/no/cancel |
| **`_CONFIRM_KEYWORDS`** | 10 个: 确认/确定/是的/好的/可以/继续/同意/办理/ok/yes |
| **`PendingAction`** | models.py:216-229, 7 字段待确认工具 (tool_name/arguments/tool_call_id/confirm_prompt/created_at/expires_at/trace_id) |
| **`confirmation_ttl_seconds`** | PendingAction TTL, 默认 300 秒 (5 分钟) |
| **5 态确认状态机** | pending / confirm / cancel / unclear / expired |
| **惰性过期 (lazy expiration)** | 无后台清扫, 每次进入 `_handle_pending_action` 时比 `expires_at` |
| **`audit_decision`** | tool_executor.py:391-408, 单独审计 confirm/cancel/expired, 区别于执行结果审计 |
| **`is_sensitive`** | MCPClient 方法, 按 `destructiveHint` 注解判断工具是否敏感 |

## A.4.5 工具护栏术语 (第 17 章)

| 名称 | 含义 |
|---|---|
| **`ToolGuard`** | tool_guard.py:35-101, 纯同步无 I/O, 角色授权 + 金额上限 |
| **`active` 属性** | tool_guard.py:41-44, allowlist 或 limits 任一非空则激活 |
| **`tool_role_allowlist`** | 角色→工具白名单, 保守模式 (未登记=无权限) |
| **`tool_amount_limits`** | 工具→金额上限, 配合 `amount_arg_keys` 检查入参 |
| **`amount_arg_keys`** | 默认 `["amount", "target_limit", "target_amount", "limit"]` |
| **`_coerce_number`** | tool_guard.py:90-101, bool 不参与 (避免 True==1), 字符串 strip 后强转 |
| **`role_denied`** | 护栏拒绝码: 角色未在白名单 |
| **`amount_exceeded`** | 护栏拒绝码: 金额超过上限 |
| **Higress 网关 vs ToolGuard** | 网关负责"谁能进" (粗粒度 token 级) + 流量治理; ToolGuard 负责"特定角色能调哪些工具 + 业务阈值" (细粒度业务级) |
| **纵深防御** | 网关层 + Python 编排侧双层防护, 不冗余, ToolGuard `active=False` 时跳过 |

## A.4.6 工具调用指标 (第 17 章)

| 名称 | 含义 |
|---|---|
| **`TOOL_CALLS`** | Counter, labels: `tool, status` (success/error), 写于 tool_executor.py:298/309 |
| **`TOOL_CONFIRMATIONS`** | Counter, labels: `decision` (pending/confirm/cancel/unclear/expired), 5 态漏斗分析 |
| **`TOOL_GUARD_DENIALS`** | Counter, labels: `tool, reason` (role_denied/amount_exceeded), 双维度交叉定位 |

## A.4.7 工具调用 PII 脱敏注入点 (第 17 章)

| 位置 | 行号 | 脱敏对象 |
|---|---|---|
| 入参脱敏 | tool_executor.py:291 | `tool_call.arguments` → 审计 detail.arguments |
| 出参脱敏 | tool_executor.py:295 | MCP 返回 content → 回喂 LLM + 审计 |
| 护栏拒绝脱敏 | tool_executor.py:379 | 拒绝时再脱敏 arguments |
| 异常脱敏 | tool_executor.py:304 | 异常时脱敏 arguments |
| 顺序敏感 | pii.py:63-76 | 身份证/银行卡 (16-19 位) 优先于手机号 (11 位), 避免 11 位数字被误判 |

## A.5 坐席辅助引擎术语

> 完整 5 阶段编排见 [第 4 章 坐席辅助](04-assist-engine.md).

| 名称 | 含义 |
|---|---|
| **D1** | 服务评估器 (decide_service) — 何时不推营销 |
| **D2** | 营销评估器 (decide_marketing) — 何时推营销 |
| **D3** | 风控评估器 (decide_risk) — 何时必推风控告警 (永不下线) |
| **E1** | AI 执行器 (ai_executor) — 话术 + RAG + 合规三路并行 |
| **E2** | 营销执行器 (marketing_executor) — 500ms 延迟避开服务期 |
| **E3** | 风控执行器 (risk / alert_engine) — 6 条种子规则 |
| **Scene** | 4 类场景: URGENT / INQUIRY / SALES / GENERAL |
| **FusionType** | 仲裁融合: BLOCK (阻断) / WARN (告警) / PASS (通过) |
| **PushTracker** | per-session 推送追踪 (last_push_at + feedback_history + min_interval) |
| **service_suppresses_marketing** | 服务激活时设置 d2_suppress_rounds=N 冷却 |
| **H2 (3s 延迟确认)** | 反馈 Redis 缓冲 3s 后提交, 期间可撤销 |

## A.6 RAG 检索术语

> 完整 RAG 链路见 [第 5 章 RAG](05-rag-pipeline.md) + [第 9 章 RAG 摄入](chapters/09-rag-ingestion.md).

| 名称 | 含义 |
|---|---|
| **BM25** | 经典词频-逆文档频率检索, ES 8.19 + IK 中文分词 |
| **IVF_FLAT** | Milvus 索引类型, 倒排文件 + 精确距离, nlist=128 |
| **nprobe** | IVF 查询时探针数, 默认 16 (查 16 个聚类桶) |
| **RRF (Reciprocal Rank Fusion)** | 倒数排名融合, 公式 `1/(k+rank)`, k=60 |
| **Parent-Child 分块** | 检索小 chunk (可嵌入), 生成拼父块 (parent_chunk_id 回填) |
| **Dual-Write** | ES + Milvus 双写, Milvus 失败回滚 ES |
| **Ollama** | 本地 LLM/嵌入推理服务, HTTP API |
| **TEI (Text Embeddings Inference)** | HuggingFace 嵌入推理服务, 批大小 128 |
| **bge-large-zh-v1.5** | 智源中文嵌入模型, 1024 维 |
| **reranker** | 重排序器, 候选 top_k*2 精排到 top_k |
| **合规字段硬注入** | 检索时自动加 approval_status=PUBLISHED + is_current_version=true + effective_date<=today |

## A.7 MCP 工具集成术语

> 完整 MCP 集成见 [第 7 章 MCP 工具](07-mcp-tool-integration.md).

| 名称 | 含义 |
|---|---|
| **MCP (Model Context Protocol)** | Anthropic 提出, AI Agent 调用工具的标准协议 |
| **streamable-http** | MCP 传输方式 (替代旧 SSE) |
| **destructiveHint** | 工具注解, true 表示敏感 (写操作) 自动加入白名单 |
| **Higress** | AI 网关, 集中鉴权/限流/脱敏/审计 |
| **Nacos** | 注册中心 + 配置中心, MCP Server 注册到 `lumio-mcp-server` 服务 |
| **lumio-mcp-server** | Java MCP Server, Spring Boot 3.4.5 + 22 工具, 端口 8090 |
| **tool_guard** | 工具护栏, role 授权 + 金额限额 |
| **destructiveHint 自动标记** | Spring AI 注解的写操作自动合并到 sensitive 白名单 |

## A.8 可观测性术语

> 完整可观测性见 [第 10 章 可观测性](chapters/10-observability.md).

| 名称 | 含义 |
|---|---|
| **Prometheus** | 指标采集系统, 9 个 scrape job (bot/assist/redis/pg/es/milvus/kafka/mcp/prom) |
| **OTLP** | OpenTelemetry Protocol, trace 上报协议 |
| **OTel (OpenTelemetry)** | 跨语言追踪/指标标准 |
| **W3C traceparent** | 跨服务 trace 上下文传递 HTTP 头 |
| **HTTPXClientInstrumentor** | 跨服务 trace 自动注入 traceparent |
| **ParentBasedTraceIdRatioSampler** | 有上游跟随上游, 无上游按 ratio 采样 |
| **Resource 属性** | service.name / namespace / version / environment |
| **PrometheusMiddleware** | FastAPI 自动打点中间件, 排除 /metrics /health /favicon.ico |
| **JSONFormatter** | 结构化日志格式化器, 含 trace_id/span_id 自动注入 |
| **PIIMaskFilter** | 日志输出侧 PII 脱敏 (手机/身份证/银行卡/邮箱) |
| **Aho-Corasick** | 多模式字符串匹配算法, O(n) 与词库大小无关, 10K 词 < 100ms |
| **audit_log** | 审计表, append-only, 4 索引 (timestamp/actor/action/target) |

## A.9 安全合规术语

> 完整安全合规见 [第 11 章 安全合规](chapters/11-security-compliance.md).

| 名称 | 含义 |
|---|---|
| **JWT (JSON Web Token)** | HS256 单 secret 签名, leeway=30s 时钟容差 |
| **RBAC (Role-Based Access Control)** | 4 角色: customer / agent / admin / service |
| **PBKDF2-HMAC-SHA256** | 密码哈希算法, 600000 次迭代 (OWASP 2023) |
| **NFKC** | Unicode 归一化形式, 全角 → 半角, 大小写归一 |
| **PII (Personally Identifiable Information)** | 个人可识别信息: 手机/身份证/银行卡/邮箱 |
| **Idempotency Key** | 幂等键参数, 避免重试导致重复受理 |
| **loopback** | 127.0.0.1 / localhost, dev bypass 限定本地访问 |
| **destructiveHint** | 工具敏感标记, 见 A.7 |
| **allowed_roles** | 文档可见角色白名单 (合规字段) |
| **regulatory_tags** | 监管标签 (合规字段) |

## A.10 数据层术语

> 完整数据层见 [第 12 章 数据层](chapters/12-data-layer.md).

| 名称 | 含义 |
|---|---|
| **PostgreSQL 16** | 主数据库, 19 张表, 12 个 Alembic 迁移 |
| **Redis 7.2** | 会话/Stream/Pub-Sub/缓存, 15+ key prefix |
| **Elasticsearch 8.19** | BM25 全文检索, IK 中文分词 (自构建镜像) |
| **Milvus 2.4** | 向量数据库, IVF_FLAT COSINE, 1024 维 |
| **MinIO** | S3 兼容对象存储, 1 bucket `lumio-docs` |
| **Kafka 3.7 (KRaft)** | 异步事件流, 单节点 ID=1, 3 topic 预留 |
| **pgcrypto** | PostgreSQL 扩展, UUID v7 生成依赖 |
| **to_tsvector** | PG 全文搜索函数, `('simple', content)` |
| **UNIQUE PARTIAL INDEX** | `WHERE is_current_version = true AND is_deleted = false` |
| **Alembic** | Python 数据库迁移工具, 12 个版本脚本 |

## A.11 部署与基础设施

> 完整部署见 [第 13 章 部署](chapters/13-deployment.md).

| 名称 | 含义 |
|---|---|
| **Docker Compose** | 24 服务编排, deploy/docker-compose.yml |
| **KRaft 模式** | Kafka 3.4+ 无 ZooKeeper 模式, 单节点即可 |
| **Jaeger 1.57** | 追踪后端, OTLP 4318 端口 |
| **Grafana 10.4** | 监控可视化, 3 套 dashboard |
| **Prometheus 2.50** | 指标采集, 9 个 scrape job, 6 告警规则 |
| **HPA (Horizontal Pod Autoscaler)** | K8s 水平自动扩缩, 2-6 replica, 70% CPU |
| **multi-stage Dockerfile** | builder 装 poetry + runtime 仅含 .venv, 减少镜像体积 |
| **livenessProbe / readinessProbe** | 存活/就绪探针, 探测 /api/health/live /ready |
| **WebSocket Upgrade** | Nginx 反代 WebSocket, 3600s timeout |
| **opt-in profile** | `make gateway-up` 手动拉起 nacos + higress, 默认不启用 |

## A.12 测试术语

> 完整测试见 [第 14 章 测试策略](chapters/14-testing-strategy.md).

| 名称 | 含义 |
|---|---|
| **pytest-asyncio** | `asyncio_mode = "auto"`, 367 个 async 测试 |
| **httpx.AsyncClient** | 异步 HTTP 客户端, 5 个 e2e fixture |
| **subprocess.Popen** | uvicorn 子进程拉起, 端口 8765/8766 避免冲突 |
| **e2e (end-to-end)** | 端到端测试, 真实中间件 + 真实子进程 |
| **覆盖率门槛** | 55% (pyproject 锁, CI 同), 留 buffer → 后续提到 60% |
| **mypy advisory** | 类型错误不阻塞 CI, 但新增错误立刻可见 |
| **pre-commit** | 8 步钩子: trailing-whitespace / eof / yaml / toml / large-files 500KB / merge-conflict / private-key / ruff |
| **pytest.skip** | 中间件不可用时跳过, 不算失败 |
| **35 errors** | 已知失败用例: 启动超时 + 无 Docker skip |

## A.13 其他通用术语

| 名称 | 含义 |
|---|---|
| **Pydantic v2** | 数据验证库, BaseModel / Field / validator |
| **Pydantic Settings** | 配置管理扩展, BaseSettings + env 读取 |
| **PydanticAI** | 类型安全 AI Agent 框架 (本项目**未使用**, 注释明确) |
| **FastAPI** | 异步 Web 框架, 0.115+ |
| **asyncio.gather** | 并发执行多个协程 (替代 Temporal) |
| **Temporal** | 工作流编排引擎 (本项目未采用, 用 asyncio.gather 替代) |
| **Circuit Breaker** | 熔断器, 三态 CLOSED / OPEN / HALF_OPEN |
| **DegradationManager** | 4 级降级 NORMAL / DEGRADED / FALLBACK |
| **slug** | 文档友好 URL 段, 例如 `credit-card-policy-v2` |
| **CRUD** | Create / Read / Update / Delete |
| **LRU (Least Recently Used)** | 最近最少使用, `@lru_cache` 单例 |
| **ContextVar** | 上下文变量, asyncio 任务间传递 (trace_id / request_id) |

## A.14 缩略语速查

| 缩写 | 全称 |
|---|---|
| **RAG** | Retrieval-Augmented Generation |
| **LLM** | Large Language Model |
| **MCP** | Model Context Protocol |
| **AI** | Artificial Intelligence |
| **ML** | Machine Learning |
| **RTO** | Recovery Time Objective |
| **RPO** | Recovery Point Objective |
| **SLO** | Service Level Objective |
| **SLA** | Service Level Agreement |
| **OTLP** | OpenTelemetry Protocol |
| **OTel** | OpenTelemetry |
| **PEL** | Pending Entries List (Redis Stream) |
| **RRF** | Reciprocal Rank Fusion |
| **JWT** | JSON Web Token |
| **PBKDF2** | Password-Based Key Derivation Function 2 |
| **NFKC** | Normalization Form KC (Compatibility Composition) |
| **PBKAC** | Password-Based Key Agreement Protocol |
| **HPA** | Horizontal Pod Autoscaler |
| **CSI** | Container Storage Interface |
| **RBAC** | Role-Based Access Control |
| **ACL** | Access Control List |
| **CSR** | Client Secret Rotation |
| **DBA** | Database Administrator |
| **DBE** | Database Engineer |
| **PII** | Personally Identifiable Information |
| **KV** | Key-Value |
| **ACID** | Atomicity, Consistency, Isolation, Durability |
| **BASE** | Basically Available, Soft state, Eventual consistency |

---

> **维护说明**: 任何新术语, 在首次出现章节加粗, 然后追加到本表对应分类. 保持与代码命名一致 (大写下划线 vs 小写蛇形).
