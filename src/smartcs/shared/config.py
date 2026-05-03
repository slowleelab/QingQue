"""全局配置管理

基于 pydantic-settings，支持环境变量覆盖。
所有配置项对应设计文档中的技术选型版本锁定。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """PostgreSQL 配置"""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_")

    host: str = "localhost"
    port: int = 5432
    user: str = "smartcs"
    password: str = "smartcs_pass"
    database: str = "smartcs"

    @property
    def dsn(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"

    @property
    def sync_dsn(self) -> str:
        return f"postgresql+psycopg2://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


class RedisSettings(BaseSettings):
    """Redis 配置"""

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    host: str = "localhost"
    port: int = 6379
    password: str = ""
    db: int = 0

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
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


class KafkaSettings(BaseSettings):
    """Kafka 配置"""

    model_config = SettingsConfigDict(env_prefix="KAFKA_")

    bootstrap_servers: str = "localhost:9094"
    knowledge_topic: str = "smartcs.knowledge.update"
    audit_topic: str = "smartcs.audit.log"
    summary_topic: str = "smartcs.call.summary"


class LLMSettings(BaseSettings):
    """大模型推理配置"""

    model_config = SettingsConfigDict(env_prefix="LLM_")

    # OpenAI 兼容 API（vLLM / Ollama）
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    primary_model: str = "qwen2.5:14b"
    fallback_model: str = "qwen2.5:7b"
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout_seconds: float = 2.0


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
    # 置信度阈值（低于此值触发兜底）
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
    debug: bool = True
    log_level: str = "INFO"

    # CORS
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # 子配置
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    elasticsearch: ElasticsearchSettings = Field(default_factory=ElasticsearchSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)
    minio: MinIOSettings = Field(default_factory=MinIOSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    classification: ClassificationSettings = Field(default_factory=ClassificationSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    assist: AssistSettings = Field(default_factory=AssistSettings)


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()
