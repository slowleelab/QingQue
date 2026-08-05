---
title: "第 2 章: 配置系统"
chapter: 2
part: "整体设计"
difficulty: "中级"
reading_time: "15 分钟"
prerequisites: ["第 1 章: 整体架构"]
code_references:
  - "agent/lumio/shared/config.py"
  - "agent/.env.example"
last_updated: "2026-08-05"
summary: "16 个 SubSettings + Pydantic v2 + env_prefix 命名空间 + AliasChoices 兼容模式 + 嵌套分隔符 __ 演示."
tags: ["config", "pydantic-settings", "env_prefix", "别名兼容"]
---

# 第 2 章: 配置系统

> 本章深入剖析 Lumio 的 16 个 SubSettings 配置体系, 这是理解项目如何"零硬编码"运行 24 个 Docker 服务 + 16+ 端口的关键.

## 2.1 配置体系总览

`agent/lumio/shared/config.py` 共 533 行, 定义 **16 个 Pydantic Settings 子类**, 加上 1 个根 `Settings` 容器.

### 2.1.1 16 个 SubSettings 一览

| # | SubSettings | env_prefix | 关键字段 | 默认值 |
|---|---|---|---|---|
| 1 | `DatabaseSettings` | `POSTGRES_` | host/port/user/password/database | localhost:5432/lumio |
| 2 | `RedisSettings` | `REDIS_` | host/port/password/db/max_connections | localhost:6379/0/20 |
| 3 | `ElasticsearchSettings` | `ES_` | hosts (列表) | localhost:9200 |
| 4 | `MilvusSettings` | `MILVUS_` | host/port/vector_dim | localhost:19530/1024 |
| 5 | `MinIOSettings` | `MINIO_` | endpoint/access_key/secret_key/bucket | localhost:9000/lumio-docs |
| 6 | `LLMSettings` | `LLM_` | base_url/api_key/primary_model/fallback_model | OpenAI 兼容 |
| 7 | `ClassificationSettings` | `CLS_` | (规划) gRPC :50051 | - |
| 8 | `RAGSettings` | `RAG_` | index_prefix/rrf_k/chunk_size | lumio/60/1500 |
| 9 | `SafetySettings` | `SAFETY_` | wordlist_path/max_scan_length | config/sensitive_words.txt |
| 10 | `SessionSettings` | `SESSION_` | 5 类超时 (idle/queue/ringing/session/review) | 1800s/60s/30s/1800s/300s |
| 11 | `BotSettings` | `BOT_` | max_concurrent_agents/message_ttl/fast_reply_cooldown | 10/8/5 |
| 12 | `AssistSettings` | `ASSIST_` | 4 类分支超时 (script/knowledge/alert/product) | 500/600/300/400 ms |
| 13 | `OrchestrationSettings` | `ORCH_` | d1_d2_d3_cooldown/e1_e2_e3_sla | 300s/3s |
| 14 | `CircuitBreakerConfigSettings` | `CB_` | ai/mkt/risk 3 套独立阈值 | 0.5/30s |
| 15 | `ObservabilitySettings` | `OBSERVABILITY_` | tracing_enabled/jaeger_host/sampling_ratio | true/localhost/1.0 |
| 16 | `MCPSettings` | `MCP_` | enabled/endpoint/timeout/sensitive_tools | false/localhost:8090/10s/4 工具 |

完整 16 个类的代码见 `config.py:16-437`.

### 2.1.2 根 `Settings` 容器

```python
# config.py:438-528 (简化)
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LUMIO_",       # 顶层 LUMIO_*
        env_nested_delimiter="__",  # 嵌套用 __ 分隔
        case_sensitive=False,
        extra="ignore",
    )

    # 16 个嵌套 SubSettings
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    elasticsearch: ElasticsearchSettings = Field(default_factory=ElasticsearchSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)
    minio: MinIOSettings = Field(default_factory=MinIOSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    classification: ClassificationSettings = Field(default_factory=ClassificationSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    bot: BotSettings = Field(default_factory=BotSettings)
    assist: AssistSettings = Field(default_factory=AssistSettings)
    orchestration: OrchestrationSettings = Field(default_factory=OrchestrationSettings)
    circuit_breaker: CircuitBreakerConfigSettings = Field(default_factory=CircuitBreakerConfigSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)

    # 顶层全局字段
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    cors_origins: list[str] = ["*"]
    jwt_secret: str = "lumio-dev-secret-change-in-production"
    dev_auth_bypass: bool = False  # P0-3 整改, 默认 False
    rate_limit_enabled: bool = True
    rate_limit_default: str = "60/minute"
    rate_limit_chat: str = "30/minute"

    @model_validator(mode="after")
    def _validate_production_security(self) -> "Settings":
        """生产环境 JWT secret 强制校验"""
        if self.environment == "production":
            forbidden = {
                "lumio-dev-secret-change-in-production",
                "<CHANGE_ME>",
                "<CHANGE_ME_IF_NEEDED>",
            }
            if self.jwt_secret in forbidden:
                raise ValueError(
                    "生产环境禁止使用占位 JWT secret. "
                    "请通过 LUMIO_JWT_SECRET 显式设置 ≥32 字符的随机值."
                )
            if len(self.jwt_secret) < 32:
                raise ValueError(
                    f"生产环境 JWT secret 至少 32 字符, 当前 {len(self.jwt_secret)}."
                )
        return self

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例 Settings 工厂"""
    return Settings()
```

## 2.2 env_prefix 命名空间: 12 个独立前缀

**关键设计**: 每个 SubSettings 独立 `env_prefix`, 而不是一个大 prefix 嵌套.

### 2.2.1 为什么不用统一前缀?

考虑两种风格对比:

| 风格 | 例子 | 优点 | 缺点 |
|---|---|---|---|
| **统一前缀嵌套** (反例) | `LUMIO_DATABASE__HOST`, `LUMIO_REDIS__HOST` | 一目了然 | 长, 同名前缀重复 |
| **独立前缀** (Lumio 风格) | `POSTGRES_HOST`, `REDIS_HOST` | 短, 业界通用 (12-factor 风格) | 需要约定 `env_prefix` |

Lumio 选择**业界通用 12-factor 风格** (Spring Boot 同款), 与 Kubernetes ConfigMap / Docker Compose env / .env.example 写法一致.

### 2.2.2 实例: `DatabaseSettings`

```python
# config.py:16-36
class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POSTGRES_", case_sensitive=False)

    host: str = "localhost"
    port: int = 5432
    user: str = "lumio"
    password: str = "lumio"
    database: str = "lumio"

    @property
    def dsn(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"
```

**读取**: `LUMIO_DATABASE__HOST=db.example.com` **不生效**, 应该用 `POSTGRES_HOST=db.example.com`. 但 `Settings.database.host` 仍能访问到 `db.example.com`, 因为 `Settings.database = Field(default_factory=DatabaseSettings)` 会自动读 `POSTGRES_*` env.

### 2.2.3 实例: `LLMSettings` (含业务约束)

```python
# config.py:91-114
class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", case_sensitive=False)

    base_url: str = "http://localhost:11434/v1"  # Ollama 兼容
    api_key: str = "ollama"
    primary_model: str = "qwen2.5:7b"
    fallback_model: str = "qwen2.5:0.5b"
    temperature: float = 0.3
    max_tokens: int = 1024
    timeout_seconds: float = 30.0
    max_concurrent: int = 5
    circuit_breaker_failures: int = 5
```

**关键设计**: 默认值指向 Ollama, 因为 dev 阶段本地 LLM 跑 qwen2.5:7b 无需 GPU 集群. 生产改 `LLM_BASE_URL=https://api.openai.com/v1` 即可.

## 2.3 嵌套分隔符 `__` 的使用

虽然 SubSettings 各自独立 `env_prefix`, 但根 `Settings` 用 `__` 嵌套分隔符提供**统一入口**:

```bash
# .env 中显式设置
LUMIO_ENVIRONMENT=production
LUMIO_DATABASE__HOST=db.prod.example.com
LUMIO_REDIS__HOST=redis.prod.example.com
LUMIO_REDIS__PASSWORD=xxx
LUMIO_BOT__MAX_CONCURRENT_AGENTS=20
LUMIO_LOG_LEVEL=WARNING
```

`Settings()` 读 `LUMIO_DATABASE__HOST` → 解析为 `database.host` → 覆盖 DatabaseSettings 的 host. 这种风格用于**部署期差异化配置**, 避免在 16 个 prefix 间切换.

**注意**: 嵌套分隔符仅在**根 `Settings` 读取时**生效, 单独 `DatabaseSettings()` 不识别 `LUMIO_DATABASE__HOST`. 实际项目里 `get_db()` 拿的是 `settings.database` (工厂构造), 因此一致.

## 2.4 AliasChoices 兼容模式 (P2-4 整改)

P2-4 之前, 项目用 `LUMIO_TRACING_ENABLED` 顶层 env 控制追踪. 整改后, 统一到 `ObservabilitySettings` 子配置, 用 `OBSERVABILITY_TRACING_ENABLED`. 但**旧 env 名不能直接放弃** — 存量 docker-compose / .env / 文档可能还在用.

### 2.4.1 兼容实现

```python
# config.py:308-326 (简化)
from pydantic import AliasChoices

class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OBSERVABILITY_", case_sensitive=False)

    tracing_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "OBSERVABILITY_TRACING_ENABLED",  # 新名
            "LUMIO_TRACING_ENABLED",          # 旧名 (兼容)
        ),
    )
    jaeger_host: str = Field(
        default="localhost",
        validation_alias=AliasChoices(
            "OBSERVABILITY_JAEGER_HOST",
            "JAEGER_HOST",  # 全局旧名
        ),
    )
    otlp_endpoint: str | None = None
    sampling_ratio: float = 1.0
```

**行为**:
- 同时设置 `OBSERVABILITY_TRACING_ENABLED=false` 和 `LUMIO_TRACING_ENABLED=true` → 选 `OBSERVABILITY_TRACING_ENABLED` (在 AliasChoices 列表靠前)
- 只设置 `LUMIO_TRACING_ENABLED=true` → 仍生效 (兼容)
- 都不设置 → 用 `default=True`

### 2.4.2 关键设计哲学

P2-4 整改的注释明确写:

> 接口稳定性 > 配置简洁性. 项目运行 6 个月后, 改 env 名 = 改 docker-compose + .env.example + 文档, 改动面 > 10 文件. 用 AliasChoices 多保留 1 行代码, 换 0 故障切换.

这是 P0-P3 整改中"接口兼容优先"的代表性决策, 详见 [附录 B](appendix/B-sprint-timeline.md#p2-4).

## 2.5 MCP 路由模式 vs 单后端模式

`MCPSettings` 走 `BaseModel` 而非 `BaseSettings`, 因为它有**结构化**配置 (路由表):

```python
# config.py:370-435 (简化)
class MCPBackend(BaseModel):
    """单后端定义"""
    name: str           # 后端名, 例如 "credit-card"
    endpoint: str       # streamable-http URL
    prefix: str = ""    # 工具名域前缀, "card." 或 ""
    sensitive_tools: list[str] = Field(default_factory=list)

class MCPSettings(BaseModel):
    """根 MCP 配置"""
    enabled: bool = False  # P0 默认 False, opt-in
    default_timeout_seconds: float = 10.0
    default_max_tool_iterations: int = 5

    # 单后端模式
    endpoint: str = "http://localhost:8090"

    # 多后端路由模式
    backends: list[MCPBackend] = Field(default_factory=list)
    route_by_tool_name: bool = False
```

### 2.5.1 单后端 vs 多后端

| 模式 | 配置示例 | 适用场景 |
|---|---|---|
| **单后端** | `MCP_ENDPOINT=http://mcp:8090` | dev / 单域 (如只有信用卡) |
| **多后端路由** | `MCP_BACKENDS=[{name: "credit-card", endpoint: ...}, {name: "loan", prefix: "loan."}]` | 多域 (信用卡 + 贷款 + 理财) |

**关键设计**: `backends=[]` 时, 自动回退到 `endpoint + prefix=""` (单后端), 保持向后兼容. 零回归切换.

## 2.6 完整环境变量清单

`agent/.env.example` 列出 16+ prefix 的全部变量. 下面是摘要 (完整见仓库根 `.env.example`):

```bash
# ─── 顶层 LUMIO_* (嵌套用 __ 分隔) ───
LUMIO_ENVIRONMENT=development
LUMIO_DEBUG=false
LUMIO_LOG_LEVEL=INFO
LUMIO_CORS_ORIGINS=*
LUMIO_JWT_SECRET=                        # 生产环境必填 ≥32 字符
LUMIO_DEV_AUTH_BYPASS=false              # P0-3 整改, 默认 False

# ─── 数据库 ───
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=lumio
POSTGRES_PASSWORD=lumio
POSTGRES_DATABASE=lumio

# ─── Redis ───
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0
REDIS_MAX_CONNECTIONS=20

# ─── Elasticsearch ───
ES_HOSTS=["http://localhost:9200"]

# ─── Milvus ───
MILVUS_HOST=localhost
MILVUS_PORT=19530
MILVUS_VECTOR_DIM=1024

# ─── MinIO ───
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=lumio-docs

# ─── LLM ───
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_PRIMARY_MODEL=qwen2.5:7b
LLM_FALLBACK_MODEL=qwen2.5:0.5b
LLM_TIMEOUT_SECONDS=30

# ─── RAG ───
RAG_INDEX_PREFIX=lumio
RAG_RRF_K=60
RAG_CHUNK_SIZE=1500
RAG_CHUNK_OVERLAP=200
RAG_BM25_TOP_K=20
RAG_VECTOR_TOP_K=20
RAG_RERANK_TOP_K=5
RAG_CACHE_TTL_SECONDS=300

# ─── 安全 ───
SAFETY_WORDLIST_PATH=config/sensitive_words.txt
SAFETY_MAX_SCAN_LENGTH=2000

# ─── 会话 ───
SESSION_BOT_IDLE_TIMEOUT=1800
SESSION_QUEUE_TIMEOUT=60
SESSION_RINGING_TIMEOUT=30
SESSION_SESSION_TIMEOUT=1800
SESSION_REVIEW_TIMEOUT=300
SESSION_MAX_TURNS=20

# ─── Bot ───
BOT_MAX_CONCURRENT_AGENTS=10
BOT_MESSAGE_TTL_SECONDS=8
BOT_FAST_REPLY_COOLDOWN=5

# ─── Assist ───
ASSIST_SCRIPT_TIMEOUT_MS=500
ASSIST_KNOWLEDGE_TIMEOUT_MS=600
ASSIST_ALERT_TIMEOUT_MS=300
ASSIST_PRODUCT_TIMEOUT_MS=400

# ─── 编排 ───
ORCH_D1_D2_D3_COOLDOWN_SECONDS=300
ORCH_E1_SLA_SECONDS=3
ORCH_E2_SLA_SECONDS=2
ORCH_E3_SLA_SECONDS=1

# ─── 熔断器 (3 套独立) ───
CB_AI_FAILURE_THRESHOLD=0.5
CB_AI_RECOVERY_TIMEOUT=30
CB_MKT_FAILURE_THRESHOLD=0.6
CB_RISK_RECOVERY_TIMEOUT=60

# ─── 可观测性 ───
OBSERVABILITY_TRACING_ENABLED=true
OBSERVABILITY_JAEGER_HOST=jaeger
OBSERVABILITY_OTLP_ENDPOINT=
OBSERVABILITY_SAMPLING_RATIO=1.0

# ─── MCP ───
MCP_ENABLED=false
MCP_ENDPOINT=http://localhost:8090
MCP_TIMEOUT_SECONDS=10
MCP_MAX_TOOL_ITERATIONS=5
MCP_SENSITIVE_TOOLS=["report_card_lost", "redeem_points", "apply_permanent_limit", "repay_credit_card"]
```

## 2.7 单例工厂 `@lru_cache`

```python
# config.py:530
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

`@lru_cache(maxsize=1)` 保证全局**单例**, 多次调用不重复构造. 任何位置 `get_settings()` 拿同一实例, 减少 Pydantic 校验开销.

## 2.8 测试策略: monkeypatch env

由于 `Settings` 在导入时构造, 测试需要**重置缓存**:

```python
# 典型测试模式
def test_settings_override(monkeypatch):
    monkeypatch.setenv("LUMIO_ENVIRONMENT", "production")
    monkeypatch.setenv("LUMIO_JWT_SECRET", "x" * 32)

    from lumio.shared.config import get_settings
    get_settings.cache_clear()  # 关键: 清 lru_cache

    settings = get_settings()
    assert settings.environment == "production"
    assert len(settings.jwt_secret) == 32
```

`test_auth.py:105` 等多处使用此模式. P0-3 整改后, 强制**生产环境 JWT secret 长度校验**, 这个测试验证了拦截逻辑.

## 2.9 本章小结

Lumio 配置系统是项目"零硬编码"的关键:

- **16 个 SubSettings + 12 个独立 env_prefix**: 业界标准 12-factor 风格
- **`__` 嵌套分隔符**: 部署期差异化配置的统一入口
- **`AliasChoices` 兼容模式**: P2-4 整改的接口稳定性策略
- **`_validate_production_security`**: 启动期 fail-fast — 生产环境强制校验 JWT 密钥 (≥32 字符, 禁占位) + **LLM_API_KEY / MINIO / ES / REDIS 五类外部凭据非默认值** (第五轮扩展, 此前仅拦 JWT, 漏配用假凭证直连外部服务)
- **分层 token 预算真实消费**: `budget_static/customer/rag/history/current` 由上下文构建器强制分配 (第 15 章), 不再是死配置
- **MCP 路由 vs 单后端**: `backends=[]` 零回归切换
- **`@lru_cache` 单例**: 减少 Pydantic 校验开销

> **下一章预告**: [第 3 章 Bot 自助问答](03-bot-self-service.md) 深入 LumioAgent 决策树, Redis Stream 全链路, 工具调用 + 确认状态机.

---

> **延伸阅读**:
> - [附录 A 术语表](appendix/A-glossary.md#a2-错误码段)
> - [第 11 章 安全合规](chapters/11-security-compliance.md) — JWT 流程细节
> - [第 13 章 部署](chapters/13-deployment.md) — 生产环境变量示例
