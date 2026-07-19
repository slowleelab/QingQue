# AGENTS.md — SmartCS Project Guide

## Project Overview

SmartCS (Smart Customer Service) is a bank credit-card intelligent customer service platform providing two core capabilities:
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
```

## Code Style & Conventions

- **Line length**: 120 (E501 ignored, handled by formatter)
- **Python target**: 3.11
- **Quotes**: double quotes
- **Ruff rules**: E, W, F, I, N, UP, B, A, SIM, RUF
- **isort**: `known-first-party = ["smartcs"]`
- **mypy**: `disallow_untyped_defs = true` on source, relaxed for tests
- **Every module** starts with `from __future__ import annotations`
- **Package layout**: package is `smartcs/` (directly under `agent/`)
- **Package manager**: Poetry — always use `poetry run` for commands
- **Pre-commit**: runs ruff (fix), ruff-format, mypy, plus generic checks (trailing whitespace, YAML/JSON validation, large files, merge conflicts, private keys)

## Key Patterns

- **App factory**: `create_bot_app()` / `create_assist_app()` — each returns a FastAPI instance with its own lifespan
- **Dependency injection**: DB engines, Redis pools, gRPC channels stored on `app.state`; injected via `Annotated[..., Depends(...)]` in `deps.py`
- **Configuration**: Pydantic-settings with 12 sub-settings classes, each with its own `env_prefix`; cached with `@lru_cache`
- **Error handling**: Hierarchical error codes (2xxx input, 3xxx business, 4xxx external, 5xxx system); global middleware maps to HTTP status codes and returns uniform `{"error": {"code", "message", "type"}}` JSON
- **Session state**: Full conversation state in Redis (SessionState model) supporting bot -> handoff -> assist -> ended lifecycle
- **RAG retrieval**: Hybrid BM25 + vector + RRF fusion with graceful degradation (BM25-only or vector-only fallback paths)
- **gRPC boundary**: AI services defined as proto contracts; orchestration layer uses generated stubs; latency tracking on every response

## Project Structure

```
agent/smartcs/            # Main package
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
      agent.py            # LangGraph agent graph
    assist/               # Agent assist
      app.py, router.py   # WS /api/ws/{session_id}, GET /api/health
      agent.py            # AssistOrchestrator
    common/               # Shared infrastructure
      database.py         # SQLAlchemy async engine
      deps.py             # FastAPI Depends injection
      grpc_clients.py     # gRPC channel pool + stubs
      redis_client.py     # Redis async connection pool
agent/proto/              # gRPC Protobuf definitions
agent/scripts/            # Init/verify utilities
deploy/                   # Docker, nginx, K8s configs
config/                   # Prometheus, Grafana, sensitive words
agent/alembic/            # DB migrations
agent/tests/              # pytest with httpx AsyncClient fixtures
```

## Testing

- **Framework**: pytest + pytest-asyncio (asyncio_mode = "auto")
- **Fixtures**: `bot_client` and `assist_client` httpx.AsyncClient fixtures in `tests/conftest.py`
- **Coverage**: 60% minimum, branch coverage enabled, source = `smartcs`
- **CI**: GitHub Actions — lint, type-check, test on every push

## Sprint Status

- Sprint 1 (completed): Infrastructure + skeleton
- Sprint 2 (completed): RAG core + knowledge base (retrieval, embedding, reranker, ingestion, chunker, dual-write)
- Sprint 3 (completed): Agent orchestration + bot MVP (LangGraph agent, chat queue, long-poll, session lifecycle)
- Sprint 4 (completed): LLM integration + degradation strategy (circuit breaker, health monitor, content degrader, OE pipeline)
- Sprint 5 (pending): Assist agent with parallel OE execution (Temporal + LangGraph DAG architecture upgrade)

## Environment Variables

All config via `.env` file or environment variables with prefixes:
`SMARTCS_`, `POSTGRES_`, `REDIS_`, `ES_`, `MILVUS_`, `MINIO_`, `KAFKA_`, `LLM_`, `CLS_`, `RAG_`, `SAFETY_`, `SESSION_`

See `.env.example` for full list with defaults.
