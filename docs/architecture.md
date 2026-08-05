# 灵智（Lumio）系统架构

> 银行信用卡智能客服平台 —— 整体架构、核心数据流与设计决策。

## 目录

- [总览](#总览)
- [三层架构](#三层架构)
- [Monorepo 子项目](#monorepo-子项目)
- [核心数据流](#核心数据流)
- [关键设计决策](#关键设计决策)
- [可观测性](#可观测性)

---

## 总览

灵智（Lumio）平台提供两大核心能力：

| 能力 | 说明 | 入口 |
|------|------|------|
| **Bot 自助服务** | 自动化对话机器人，基于 RAG + 意图分类 + Agent 编排处理客户咨询 | Bot Service `:8000` |
| **AI 坐席辅助** | 通话中向人工坐席实时推送话术 / 知识 / 合规告警 / 商品推荐 | Assist Service `:8001`（WebSocket） |

系统采用**编排层与 AI 能力层分离**的设计：FastAPI 应用只负责会话编排与业务流转，重型 AI 能力（分类、检索、安全过滤）以 gRPC 契约定义，可独立部署与伸缩。

## 三层架构

```
                        ┌─────────────────────────────────────────────────────────────┐
                        │            ACCESS · 接入层 (Web · Java)                       │
                        │                                                             │
                        │   /              /agent         /admin           /login      │
                        │   CustomerChat   Workbench      AdminLayout      LoginPage   │
                        │   (Vue 3)        (Vue 3)        (Vue 3)          (Vue 3)     │
                        │                                                             │
                        │   customer-server :8080   agent-server :8081                 │
                        │   (Java chat-svc, Netty + ZooKeeper)                  │
                        └──────────────────────┬──────────────────────────────────────┘
                                               │  HTTP / WS
                        ┌──────────────────────▼──────────────────────────────────────┐
                        │        ORCHESTRATION · 编排层 (Python · FastAPI)            │
                        │                                                             │
                        │  agent/lumio/main.py                                       │
                        │      ├─ bot_app  :8000  (bot 异步聊天 + 长轮询)             │
                        │      └─ assist_app:8001  (WS 推送 · OE 仲裁)                │
                        │                                                             │
                        │  services/bot/   · bot_agent · tool_executor · tool_guard   │
                        │  services/assist/· ai_executor · summary · alert_engine     │
                        │  services/common/· session · retrieval · ingestion · auth   │
                        │  shared/        · config · exceptions · metrics · tracing  │
                        └──────────────────────┬──────────────────────────────────────┘
                                               │  gRPC
                        ┌──────────────────────▼──────────────────────────────────────┐
                        │      AI CAPABILITY · AI 能力层 (proto package: lumio)       │
                        │                                                             │
                        │   classification :50051   (意图/情绪/领域)                  │
                        │   retrieval      :50052   (BM25 + 向量 + RRF)               │
                        │   safety         :50053   (敏感词 / PII)                    │
                        │                                                             │
                        │  Java MCP Server com.lumio.mcp  :8090  (22 tools, mock)    │
                        └──────────────────────┬──────────────────────────────────────┘
                                               │
                        ┌──────────────────────▼──────────────────────────────────────┐
                        │            DATA · 数据层 (deploy/docker-compose.yml)         │
                        │                                                             │
                        │  lumio-postgres   PostgreSQL 16 + PostGIS 3.4               │
                        │  lumio-redis      Redis 7.2                                  │
                        │  lumio-elasticsearch  8.19 + IK                            │
                        │  lumio-milvus     2.4 + etcd + minio                        │
                        │  lumio-minio      对象存储                                  │
                        │  lumio-kafka      KRaft 3.7                                │
                        │                                                             │
                        │  监控：lumio-prometheus · lumio-grafana · lumio-jaeger      │
                        │  网关：lumio-higress · lumio-nacos  (gateway profile)       │
                        │  网络：lumio-net                                              │
                        └─────────────────────────────────────────────────────────────┘
```

### 编排层（`agent/lumio/services/`）

- **每个服务是独立的 FastAPI app 工厂**：`bot_app` / `assist_app`，由 `agent/lumio/main.py` 暴露，各有独立 lifespan。
- **依赖注入**：DB engine、Redis 连接池、gRPC channel 存于 `app.state`，经 `Annotated[..., Depends(...)]` 注入（见 `services/common/deps.py`）。
- **共享基础设施**集中在 `services/common/`：检索、embedding、reranker、会话、降级、熔断、审计、PII、auth_router 等 25 个模块。
- **Java 坐席集成**：`chat-svc/customer-server:8080` 与 `agent-server:8081` 通过 `LumioClient`（Java 端）和 `LumioSessionListener` 与 Lumio 双向通信；子阶段方法 `toLumioSubPhase()` 取代历史 `toSmartcsSubPhase()`。

### AI 能力层

以 Protobuf 定义三个服务契约（`proto package lumio`），编排层通过生成的 stub 调用，并对每次调用做延迟追踪：

- `classification.proto` — 意图 / 情绪 / 领域分类
- `retrieval.proto` — 混合检索（BM25 + 向量 + RRF）
- `safety.proto` — 敏感词 / 合规过滤

Java 侧 `mcp-server` 暴露 **22 个信用卡工具**（账单/卡服务/额度/分期/还款/积分/交易），全部返回 mock 数据，对接 Spring AI MCP Server，端口 8090（SSE），`prod` profile 注册到 Nacos。Python 端通过 `LumioToolClient` 走 streamable-http，Higress 桥接 SSE ↔ streamable-http。

> 该 gRPC 层当前为契约定义，编排层内置了等价的本地实现作为兜底（降级策略见下文）。

### 数据层（`deploy/docker-compose.yml`）

| 容器 | 镜像 | 用途 |
|------|------|------|
| `lumio-postgres` | `postgres:16` + PostGIS 3.4 | 业务真相源（会话、知识、规则、审计） |
| `lumio-redis` | `redis:7.2-alpine` | 会话状态、缓存、Pub/Sub 热加载、反馈缓冲 |
| `lumio-elasticsearch` | `lumio/elasticsearch-ik:8.19.9` | 知识全文检索（BM25），中文分词 |
| `lumio-milvus` | `milvusdb/milvus:v2.4.0` | 向量检索（bge-large-zh embedding） |
| `lumio-minio` | `minio/minio` | 原始文档对象存储 |
| `lumio-kafka` | `apache/kafka:3.7.0` | 异步 ETL / 事件流（KRaft 模式） |
| `lumio-prometheus` | `prom/prometheus:v2.50.0` | 指标聚合（端口 9090） |
| `lumio-grafana` | `grafana/grafana:10.4.0` | 监控看板（端口 3001） |
| `lumio-jaeger` | jaeger 1.57 | 链路追踪（opt-in profile） |
| `lumio-higress` | `higress/all-in-one:2.1.5` | AI 网关（gateway profile） |
| `lumio-nacos` | `nacos/nacos-server:v2.4.3` | 服务发现 + MCP Registry（gateway profile） |
| `mcp-server` | `lumio-mcp-server:1.0.0`（本地构建） | Java 信用卡 MCP 工具服务，端口 8090 |

所有服务加入 `lumio-net` 网络，镜像来自 `docker.io/slowleelab/...`。

## Monorepo 子项目

| 目录 | 语言 | 说明 |
|------|------|------|
| `agent/` | Python 3.11 | 灵智（Lumio）核心：Bot + Assist 编排服务，主包 `lumio`（历史名 `smartcs`） |
| `mcp-server/` | Java + Spring AI | Java MCP Server，22 个信用卡工具（全部 mock） |
| `chat-svc/` | Java | 在线客服接入系统（customer-server :8080 / agent-server :8081） |
| `kb-service/` | Python | 独立知识数据微服务（ES 原生 RRF，取代 Milvus 双写） |
| `web/` | Vue 3 + TS | 坐席工作台 / 客户对话前端，路由 `/`、`/agent`、`/login`、`/admin` |

## 核心数据流

### Bot 对话（自助服务）

```
客户消息
  → POST /api/chat/send
  → L1 规则快速意图匹配（RuleLoader，<5ms）
  → Bot Agent 编排（asyncio 并行 + 规则路由）
       ├─ 意图分类（CLS gRPC）
       ├─ 混合检索（BM25 ⊕ 向量，RRF 融合 → Reranker）
       ├─ LLM 生成（Qwen2.5，含降级）
       └─ 安全过滤（敏感词 + PII 脱敏）
  → 响应入队 → GET /api/chat/poll（长轮询）
  → 命中转人工关键词 → POST /api/chat/transfer → 调用 LumioClient 转人工
  → 切换 phase=AGENT（亚阶段 agent:queued → agent:assigned → agent:active）
```

### 坐席辅助（实时推送）

```
通话音频 / 客户消息
  → WS /api/ws/{session_id}（customer_message）
  → OE Pipeline 并行评估（D1 服务 / D2 营销 / D3 风险）
  → GlobalArbitrator 全局仲裁（风险优先，可阻断营销）
  → AssistOrchestrator 组装推送载荷
       （话术卡 / 知识摘要 / 合规告警 / 商品推荐）
  → WS 推送 assist_push 给坐席
  → 坐席反馈 POST /api/feedback → Redis 缓冲（3s 延迟提交，可撤销）
```

### 会话状态机

会话采用 **3 阶段 × 7 子状态**模型（`BOT → AGENT → ENDED`），完整状态存于 Redis（`SessionState`），key 前缀 `lumio:session:{id}:meta` 与 `lumio:session:{id}:history`。阶段统一为 `bot: / agent: / ended` 三个 phase，每个 phase 下含若干 sub_phase，转换由 `validate_transition()` 校验，非法转换抛 `LumioError`。

## 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| **混合检索** | BM25 + 向量 + RRF 融合 | 兼顾精确关键词与语义召回；支持 BM25-only / 向量-only 降级 |
| **降级策略** | 熔断器 + 健康监控 + 内容降级 | LLM/检索故障时自动切换兜底路径，保证可用性 |
| **会话状态** | Redis 全量存储 | 无状态编排层可水平扩展，状态共享 |
| **OE 仲裁** | 风险优先级最高 | 金融风险拦截可覆盖营销/服务推荐，合规第一 |
| **反馈闭环** | Redis 缓冲 + 3s 延迟提交 | 坐席可在延迟期内撤销误操作反馈 |
| **异常基类** | `LumioError`（24 个子类） | 按错误码层级映射 HTTP 状态 |
| **AI 网关** | Higress + Nacos 单平面 | SSE ↔ streamable-http 桥接，统一 MCP 数据面 + 治理 |

## 可观测性

### Prometheus 指标（16 条，`agent/lumio/shared/metrics.py`）

| 指标 | 类型 | 用途 |
|------|------|------|
| `lumio_request_count` | Counter | HTTP 请求量（method/path/status） |
| `lumio_request_duration_seconds` | Histogram | HTTP 请求延迟 |
| `lumio_fast_reply_total` | Counter | 快速回复命中 |
| `lumio_agent_responses_total` | Counter | Agent 响应来源分布 |
| `lumio_bot_semaphore_utilization` | Gauge | Bot 信号量利用率 |
| `lumio_active_workers` | Gauge | 当前活跃 worker 数 |
| `lumio_stream_length` | Gauge | 流式响应长度 |
| `lumio_stream_pending_total` | Counter | 流式待处理数 |
| `lumio_circuit_breaker_state` | Gauge | 熔断器状态 |
| `lumio_circuit_breaker_transitions_total` | Counter | 熔断器跳变次数 |
| `lumio_assist_engine_decisions_total` | Counter | Assist 引擎决策数 |
| `lumio_assist_engine_latency_seconds` | Histogram | Assist 引擎延迟 |
| `lumio_assist_engine_degradation_total` | Counter | Assist 降级次数 |
| `lumio_tool_calls_total` | Counter | 工具调用次数 |
| `lumio_tool_call_duration_seconds` | Histogram | 工具调用延迟 |
| `lumio_llm_call_duration_seconds` | Histogram | LLM 调用延迟 |

### Grafana 看板

- `config/grafana/lumio-overview.json` — 顶层系统健康
- `config/grafana/lumio-dashboard.json` — 各服务深度看板

### 告警

- `config/prometheus/rules/alerts.yml` — 4 个告警组：`lumio-availability` / `lumio-tools` / `lumio-latency` / `lumio-sessions`

### 链路与日志

- **链路**：OpenTelemetry tracing（`shared/tracing.py`），数据上报到 `lumio-jaeger`
- **日志**：JSON 结构化日志（`shared/logger.py`）
- **监控单一事实源**：[`config/`](../config/)，由 `deploy/docker-compose.yml` 挂载

---

## 延伸阅读

- [API 参考](./api-reference.md) — REST / WebSocket 接口
- [部署指南](./deployment.md) — 中间件与服务启动
- [配置参考](./configuration.md) — `LUMIO_*` 环境变量全览
- 技术深度剖析：[`book/README.md`](./book/README.md) — 21 章电子书
