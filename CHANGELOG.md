# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

## [Unreleased] - 2026-07-30

### Changed
- **重命名：SmartCS → Lumio / 灵智**（跨 3 个 commit）
  - `4be1d67` — Python 包 `smartcs` → `lumio`，异常类 `SmartCSError` → `LumioError`（24 子类同改）
  - `0908f82` — 资源 / 容器 / 镜像 / Java mcp-server / star-connection / proto / web 前端重命名；Java groupId `com.smartcs` → `com.lumio`
  - (本 commit) — docs / history / UAT / blog 全部重写

### Migration
- env var: `SMARTCS_*` → `LUMIO_*`（16 个变量 + pydantic env_prefix）
- PG db/user: `smartcs` → `lumio`，pass: `smartcs_pass` → `lumio_pass`
- Redis key namespace: `smartcs:*` → `lumio:*`（旧数据需 RENAME 或重建）
- Prometheus metric: `smartcs_*` → `lumio_*`（历史数据分裂，可加 alias）
- Grafana dashboard: `smartcs-{overview,dashboard}.json` → `lumio-*`
- web localStorage: `smartcs_token` → `lumio_token`（含自动迁移 shim）
- K8s manifest: `deploy/k8s/smartcs.yaml` → `lumio.yaml`
- Java class: `SmartcsClient` → `LumioClient`，`SmartcsSessionListener` → `LumioSessionListener`
- 异常类: `SmartCSError` → `LumioError`

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
