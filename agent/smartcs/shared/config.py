"""全局配置管理

基于 pydantic-settings，支持环境变量覆盖。
所有配置项对应设计文档中的技术选型版本锁定。
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """PostgreSQL 配置"""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_")

    host: str = "localhost"
    port: int = 5432
    user: str = "smartcs"
    password: str = "smartcs_pass"
    database: str = "smartcs"
    pool_size: int = 5
    max_overflow: int = 10

    @property
    def dsn(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{quote_plus(self.password)}@{self.host}:{self.port}/{self.database}"

    @property
    def sync_dsn(self) -> str:
        return f"postgresql+psycopg2://{self.user}:{quote_plus(self.password)}@{self.host}:{self.port}/{self.database}"


class RedisSettings(BaseSettings):
    """Redis 配置"""

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    host: str = "localhost"
    port: int = 6379
    password: str = ""
    db: int = 0
    max_connections: int = 20

    @property
    def url(self) -> str:
        auth = f":{quote_plus(self.password)}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class ElasticsearchSettings(BaseSettings):
    """Elasticsearch 配置"""

    model_config = SettingsConfigDict(env_prefix="ES_")

    hosts: str = "http://localhost:9200"
    # 开发环境关闭安全认证，以下仅生产环境使用
    username: str = ""
    password: str = ""
    index_prefix: str = "smartcs"
    verify_certs: bool = False


class MilvusSettings(BaseSettings):
    """Milvus 配置"""

    model_config = SettingsConfigDict(env_prefix="MILVUS_")

    host: str = "localhost"
    port: int = 19530
    collection_name: str = "smartcs_knowledge"
    vector_dim: int = 1024  # bge-large-zh-v1.5 输出维度


class MinIOSettings(BaseSettings):
    """MinIO 配置"""

    model_config = SettingsConfigDict(env_prefix="MINIO_")

    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    bucket: str = "smartcs-docs"
    secure: bool = False


class LLMSettings(BaseSettings):
    """大模型推理配置"""

    model_config = SettingsConfigDict(env_prefix="LLM_")

    # OpenAI 兼容 API（vLLM / Ollama）
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    primary_model: str = "qwen2.5:7b"
    fallback_model: str = "qwen2.5:7b"
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout_seconds: float = 60.0

    # 健康探测
    health_probe_interval_seconds: float = 1.0  # 初始探测间隔
    health_probe_max_interval: float = 30.0  # 指数退避上限
    health_probe_timeout: float = 5.0  # 探测超时
    health_probe_fail_threshold: int = 2  # 连续失败降级阈值
    health_probe_success_threshold: int = 2  # 连续成功恢复阈值
    # 各类独立超时
    classify_timeout: float = 1.5  # 分类独立超时
    generate_timeout: float = 2.0  # 生成独立超时


class ClassificationSettings(BaseSettings):
    """分类模型配置"""

    model_config = SettingsConfigDict(env_prefix="CLS_")

    # gRPC 服务地址
    grpc_host: str = "localhost"
    grpc_port: int = 50051
    # 意图分类置信度阈值
    intent_threshold: float = 0.6
    # 实体抽取
    min_entity_confidence: float = 0.7


class RAGSettings(BaseSettings):
    """RAG 检索配置"""

    model_config = SettingsConfigDict(env_prefix="RAG_")

    # gRPC 服务地址
    grpc_host: str = "localhost"
    grpc_port: int = 50052
    # 检索参数
    top_k: int = 5
    rerank: bool = True
    # RRF 融合参数
    rrf_k: int = 60
    # RRF 置信度阈值（RRF 分数范围 ~0.005-0.05，远低于 Reranker 的 0-1）
    rrf_confidence_threshold: float = 0.0
    # Reranker 置信度阈值（Reranker 分数范围 0-1）
    confidence_threshold: float = 0.5
    # Embedding 模型
    embedding_provider: str = "ollama"  # ollama / tei
    embedding_model: str = "mxbai-embed-large"  # Ollama 开发环境模型
    tei_embedding_model: str = "BAAI/bge-M3"  # TEI 生产环境模型
    embedding_dim: int = 1024
    embedding_batch_size: int = 128  # TEI 批量大小
    tei_base_url: str = "http://localhost:8080"  # TEI 服务地址
    embedding_timeout: float = 10.0  # 嵌入请求超时（秒）
    embedding_max_retries: int = 2  # 最大重试次数
    # 分块参数
    chunk_size: int = 1500  # 字符数，约 750 中文字 ≈ 1000+ tokens
    chunk_overlap: int = 200  # 字符数
    # 重排模型
    reranker_model: str = "bge-reranker-v2-m3"
    reranker_provider: str = "ollama"  # ollama / tei
    # 入库锁
    ingestion_lock_ttl: int = 600  # 分布式锁 TTL（秒）


class SafetySettings(BaseSettings):
    """安全过滤配置"""

    model_config = SettingsConfigDict(env_prefix="SAFETY_")

    grpc_host: str = "localhost"
    grpc_port: int = 50053
    # 敏感词文件路径
    sensitive_words_path: str = "config/sensitive_words.txt"
    # 脱敏规则
    enable_phone_mask: bool = True
    enable_id_card_mask: bool = True
    enable_bank_card_mask: bool = True


class SessionSettings(BaseSettings):
    """会话状态配置"""

    model_config = SettingsConfigDict(env_prefix="SESSION_")

    # Redis key 前缀
    key_prefix: str = "session:"
    # 会话 TTL（秒）
    ttl_seconds: int = 1800  # 30 分钟
    # 对话历史窗口大小
    max_turns: int = 20
    # 低置信度连续计数阈值（L3 触发）
    low_confidence_threshold: int = 3
    # 超时配置（秒）
    bot_idle_timeout: int = 120  # BOT 阶段空闲超时
    queue_timeout: int = 60  # AG_QUEUED 排队超时（超时回退 BOT）
    ringing_timeout: int = 30  # AG_ASSIGNED 振铃超时
    session_timeout: int = 1800  # AG_ACTIVE 会话总时长超时
    review_timeout: int = 120  # AG_REVIEWING 话后小结超时


class BotSettings(BaseSettings):
    """Bot 服务配置"""

    model_config = SettingsConfigDict(env_prefix="BOT_")

    # 最大并发 Agent 数（Semaphore 槽位）
    max_concurrent_agents: int = 10
    # 消息过期时间（秒），超过此时间的消息跳过处理
    message_ttl_seconds: int = 8
    # fast_reply 冷却时间（秒），同一会话两次 fast_reply 的最小间隔
    fast_reply_cooldown: int = 5


class AssistSettings(BaseSettings):
    """坐席辅助配置"""

    model_config = SettingsConfigDict(env_prefix="ASSIST_")

    # 分支超时（毫秒）
    script_timeout_ms: int = 500
    knowledge_timeout_ms: int = 600
    alert_timeout_ms: int = 300
    product_timeout_ms: int = 400

    # 推送节流
    throttle_window_ms: int = 800

    # 话术
    polish_model: str = "qwen2.5:7b"
    script_cache_ttl: int = 300  # Redis 缓存秒数
    max_scripts_per_push: int = 3

    # 知识
    max_knowledge_per_push: int = 3

    # 情绪趋势
    sentiment_trend_window: int = 3  # 连续负面轮数触发升级

    # 产品
    max_recommendations_per_push: int = 2


class OrchestrationSettings(BaseSettings):
    """编排层配置（对应设计文档 §3.3）"""

    model_config = SettingsConfigDict(env_prefix="ORCH_")

    # 评估器冷却轮数（对应文档 §3.3 三路评估器表）
    d1_cooldown_turns: int = 2
    d2_cooldown_turns: int = 5
    d3_always_active: bool = True

    # 评估器激活阈值
    d1_intent_confidence_threshold: float = 0.5
    d2_emotion_score_threshold: float = 0.3

    # 全局超时（对应文档 §3.5 仲裁超时兜底）
    global_timeout_ms: int = 5000

    # 执行器 SLA（对应文档 §3.4 执行器 SLA 表）
    e1_sla_ms: int = 3000  # AI 服务
    e2_sla_ms: int = 500  # 营销
    e3_sla_ms: int = 100  # 风控

    # 营销延迟（对应文档 §3.3 策略: marketing_deferred）
    marketing_defer_ms: int = 500

    # PushTracker 基础推送间隔（秒）
    base_interval_ai: float = 3.0
    base_interval_marketing: float = 30.0

    # 动态间隔调整系数（坐席反馈驱动）
    adoption_shorten_ratio: float = 0.5  # 连续采纳3次→间隔×0.5
    dismiss_extend_ratio: float = 2.0    # 坐席关闭→间隔×2.0
    ignore_extend_ratio: float = 1.5     # 连续忽略3次→间隔×1.5


class TemporalSettings(BaseSettings):
    """Temporal 配置"""

    model_config = SettingsConfigDict(env_prefix="TEMPORAL_")

    host: str = "localhost"
    port: int = 7233
    namespace: str = "default"
    task_queue: str = "smartcs-assist"
    workflow_timeout_seconds: int = 10


class CircuitBreakerConfigSettings(BaseSettings):
    """熔断器配置（各执行器独立，对应文档 §3.4 熔断器配置表）"""

    model_config = SettingsConfigDict(env_prefix="CB_")

    # AI 服务执行器
    ai_failure_rate_threshold: float = 0.5
    ai_slow_call_rate_threshold: float = 0.6
    ai_slow_call_duration_ms: int = 3000
    ai_wait_duration_open_s: float = 30.0
    ai_half_open_max_calls: int = 3
    ai_sliding_window_size: int = 20

    # 营销执行器
    mkt_failure_rate_threshold: float = 0.5
    mkt_slow_call_rate_threshold: float = 0.5
    mkt_slow_call_duration_ms: int = 500
    mkt_wait_duration_open_s: float = 20.0
    mkt_sliding_window_size: int = 20

    # 风控执行器
    risk_failure_rate_threshold: float = 0.3
    risk_slow_call_rate_threshold: float = 0.3
    risk_slow_call_duration_ms: int = 100
    risk_wait_duration_open_s: float = 10.0
    risk_sliding_window_size: int = 20


class Settings(BaseSettings):
    """全局配置根"""

    model_config = SettingsConfigDict(
        env_prefix="SMARTCS_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务标识
    service_name: str = "smartcs"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    service_host: str = "127.0.0.1"

    # star-connection 客户端
    star_connection_url: str = "http://localhost:8080"

    # CORS
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # 认证（JWT）
    jwt_secret: str = "smartcs-dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30  # access token 30 分钟
    jwt_refresh_expire_days: int = 7  # refresh token 7 天

    @model_validator(mode="after")
    def _validate_production_security(self) -> Settings:
        """生产环境安全检查: 拒绝使用默认 JWT 密钥启动"""
        if self.environment == "production":
            if self.jwt_secret == "smartcs-dev-secret-change-in-production":
                raise ValueError("生产环境必须设置 SMARTCS_JWT_SECRET 环境变量，" "不能使用默认开发密钥")
            if self.jwt_secret and len(self.jwt_secret) < 32:
                raise ValueError("生产环境 JWT 密钥长度必须 >= 32 字符")
        return self

    # 限流
    rate_limit_enabled: bool = True
    rate_limit_default: str = "60/minute"
    rate_limit_chat: str = "30/minute"

    # 子配置
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    elasticsearch: ElasticsearchSettings = Field(default_factory=ElasticsearchSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)
    minio: MinIOSettings = Field(default_factory=MinIOSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    classification: ClassificationSettings = Field(default_factory=ClassificationSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    bot: BotSettings = Field(default_factory=BotSettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    assist: AssistSettings = Field(default_factory=AssistSettings)
    orchestration: OrchestrationSettings = Field(default_factory=OrchestrationSettings)
    temporal: TemporalSettings = Field(default_factory=TemporalSettings)
    circuit_breaker: CircuitBreakerConfigSettings = Field(default_factory=CircuitBreakerConfigSettings)


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()
