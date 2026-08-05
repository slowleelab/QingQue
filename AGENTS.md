# AGENTS.md — Lumio / 灵智 Project Guide

## Project Overview

Lumio (灵智) is a bank credit-card intelligent customer service platform providing two core capabilities:
- **AI Agent Assist** — real-time suggestions/knowledge/compliance alerts pushed to human agents via WebSocket during live calls
- **Bot Self-Service** — automated conversational bot handling customer inquiries via RAG, intent classification, and Agent orchestration

All user-facing strings and docstrings are in Chinese. Variable/function names are in English.

## Architecture

Three-layer architecture:
1. **Orchestration Layer** (FastAPI): Bot Service (:8000) + Assist Service (:8001), each as independent FastAPI app instances
2. **AI Capability Layer** (gRPC, proto-defined, not yet implemented): ClassificationService, RetrievalService, SafetyFilterService
3. **Data Layer** (Docker Compose): PostgreSQL 16, Redis 7.2, Elasticsearch 8.19+IK, Milvus 2.4, MinIO, Kafka 3.7 (KRaft)

## Common Commands

```bash
make install        # Install dependencies (Poetry)
make dev            # Start both services (bot :8000 + assist :8001) with --reload
make test           # Run pytest
make test-cov       # Run pytest with coverage (60% minimum)
make lint           # Ruff check + fix
make format         # Ruff format
make type-check     # mypy on src/
make pre-commit     # Install & run pre-commit hooks
make up             # Start all middleware (Docker Compose)
make down           # Stop all middleware
make init           # Initialize Milvus + ES + Kafka
make verify         # Verify middleware connectivity
make proto          # Compile gRPC proto files
make migrate        # Run Alembic migrations
make migrate-create # Create new migration (msg="description")
make mcp-server-build # Build Java MCP Server (mcp-server/)
make mcp-server-test  # Run Java MCP Server unit tests
make mcp-server-run   # Run Java MCP Server (SSE :8090, mock data)
make gateway-up       # Start Higress AI gateway + Nacos (opt-in 'gateway' profile)
make gateway-down     # Stop Higress + Nacos
```

## Code Style & Conventions

- **Line length**: 120 (E501 ignored, handled by formatter)
- **Python target**: 3.11
- **Quotes**: double quotes
- **Ruff rules**: E, W, F, I, N, UP, B, A, SIM, RUF
- **isort**: `known-first-party = ["lumio"]`
- **mypy**: `disallow_untyped_defs = true` on source, relaxed for tests
- **Every module** starts with `from __future__ import annotations`
- **Package layout**: package is `lumio/` (directly under `agent/`, renamed from `smartcs/` in commit 4be1d67)
- **Package manager**: Poetry — always use `poetry run` for commands
- **Pre-commit**: runs ruff (fix), ruff-format, mypy, plus generic checks (trailing whitespace, YAML/JSON validation, large files, merge conflicts, private keys)

## Key Patterns

- **App factory**: `create_bot_app()` / `create_assist_app()` — each returns a FastAPI instance with its own lifespan
- **Dependency injection**: DB engines, Redis pools, gRPC channels stored on `app.state`; injected via `Annotated[..., Depends(...)]` in `deps.py`
- **Configuration**: Pydantic-settings with 12 sub-settings classes, each with its own `env_prefix`; cached with `@lru_cache`
- **Error handling**: Hierarchical error codes (2xxx input, 3xxx business, 4xxx external, 5xxx system); global middleware maps to HTTP status codes and returns uniform `{"error": {"code", "message", "type"}}` JSON
- **Session state**: Full conversation state in Redis (SessionState model) supporting bot -> handoff -> assist -> ended lifecycle; 每个画像字段含 `*_updated_at` 时间戳 (D0 衰减 fallback 0.0 → 999 天 → 强制降级)
- **RAG retrieval**: Hybrid BM25 + vector + RRF fusion with graceful degradation (BM25-only or vector-only fallback paths)
- **gRPC boundary**: AI services defined as proto contracts; orchestration layer uses generated stubs; latency tracking on every response
- **Decision log (E2)**: 每条决策双写 Redis（最近 100 条实时查询）+ PG `decision_log` 表（alembic `c7d8e9f0a1b2`，3 复合索引支持客户查询/监管审计/GDPR 删除），后台 task 持有引用防 GC
- **Token estimate**: 统一通过 `shared/token_utils.estimate_tokens(text, base_overhead=N)` 入口（CJK/拉丁字符类感知系数），`bot_agent` 默认 `base_overhead=4`（消息格式开销），禁止各模块自行实现
- **后台 task 规范**: 所有 `asyncio.create_task` 必须持有引用（`_pending_tasks: set` + `add_done_callback(discard)`），防 asyncio GC 在 `await` 期间回收 task；Redis 配额用 Lua 原子 INCR+EXPIRE 避免 key 永不过期
- **WS 错误处理**: `WebSocketDisconnect`/`Exception` 时返回 `{type: error, code, trace_id, message}` 通用文案，绝不把 `str(exc)` 透传给客户端（防信息泄露）

## Project Structure

```
agent/lumio/               # Main package (renamed from smartcs)
  main.py                 # App factories + lifespan managers
  shared/                 # Cross-cutting modules
    config.py             # Pydantic-settings (12 sub-configs)
    exceptions.py         # Error code hierarchy
    logger.py             # JSON structured logging
    middleware.py          # Global exception handler + RequestValidationError
    models.py             # 15+ Pydantic models
  services/
    bot/                  # Bot self-service
      app.py, router.py   # POST /api/chat, GET /api/health
      bot_agent.py        # Bot agent (asyncio + rule routing)
    assist/               # Agent assist
      app.py, router.py   # WS /api/ws/{session_id}, GET /api/health
      ai_executor.py      # AI executor (PydanticAI)
    common/               # Shared infrastructure
      database.py         # SQLAlchemy async engine
      deps.py             # FastAPI Depends injection
      assist_engine.py    # 坐席辅助引擎 (asyncio.gather + PydanticAI)
      grpc_clients.py     # gRPC channel pool + stubs
      redis_client.py     # Redis async connection pool
      mcp_client.py       # MCP tool client (MCPToolClient) — connects Higress MCP gateway
    tools/                # Reference MCP server (FastMCP) — mock credit-card tools for local trial/tests
agent/proto/              # gRPC Protobuf definitions
agent/scripts/            # Init/verify utilities
mcp-server/               # Java MCP Server (Spring AI, standalone) — 22 credit-card tools, mock data only
deploy/                   # Docker, nginx, K8s configs
config/                   # Prometheus, Grafana, sensitive words
agent/alembic/            # DB migrations
agent/tests/              # pytest with httpx AsyncClient fixtures
```

## Testing

- **Framework**: pytest + pytest-asyncio (asyncio_mode = "auto")
- **Fixtures**: `bot_client` and `assist_client` httpx.AsyncClient fixtures in `tests/conftest.py`
- **Coverage**: 60% minimum, branch coverage enabled, source = `lumio`
- **CI**: GitHub Actions — lint, type-check, test on every push

## Sprint Status

- Sprint 1 (completed): Infrastructure + skeleton
- Sprint 2 (completed): RAG core + knowledge base (retrieval, embedding, reranker, ingestion, chunker, dual-write)
- Sprint 3 (completed): Agent orchestration + bot MVP (asyncio agent + rule routing, chat queue, long-poll, session lifecycle)
- Sprint 4 (completed): LLM integration + degradation strategy (circuit breaker, health monitor, content degrader, assist engine)
- Sprint 5 (completed): Assist engine with parallel D/E execution (asyncio.gather + PydanticAI; Temporal removed)
- Sprint 6 (completed): 架构师深度审核 + 19 项修复 (3 P0 + 9 P1 + 7 P2). 详见 `CHANGELOG.md` 与 `docs/P0_delivery_checklist.md` v1.6.1 章节. 关键修复: DecisionLog PG 落库 (E2 持久化) + token_utils 统一 + GC-safe 后台 task + WS 错误信息脱敏 + 实体白名单双源一致 + 配额 Lua 原子化. 单元测试 713 passed / 0 failed.

## Environment Variables

All config via `.env` file or environment variables with prefixes:
`LUMIO_`, `POSTGRES_`, `REDIS_`, `ES_`, `MILVUS_`, `MINIO_`, `KAFKA_`, `LLM_`, `CLS_`, `RAG_`, `SAFETY_`, `SESSION_`

See `.env.example` for full list with defaults.
