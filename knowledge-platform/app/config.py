"""全局配置管理

基于 pydantic-settings，支持环境变量覆盖。
架构：PG(真相源) + ES(BM25+IK ‖ kNN 原生 RRF) + Reranker + Kafka + MinIO + Redis
无 Milvus，无双写，无手写 RRF。
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """PostgreSQL — 唯一真相源"""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_")

    host: str = "localhost"
    port: int = 5433
    user: str = "kp_user"
    password: str = "kp_pass"
    database: str = "knowledge_platform"
    pool_size: int = 5
    max_overflow: int = 10

    @property
    def dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    @property
    def sync_dsn(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class RedisSettings(BaseSettings):
    """Redis — 检索缓存"""

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    host: str = "localhost"
    port: int = 6380
    password: str = ""
    db: int = 0
    max_connections: int = 20

    @property
    def url(self) -> str:
        auth = f":{quote_plus(self.password)}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class ElasticsearchSettings(BaseSettings):
    """Elasticsearch — BM25+IK 关键词召回 ‖ kNN 向量召回 ‖ 原生 RRF 融合

    ES 是可从 PG 重建的派生索引，非真相源。
    """

    model_config = SettingsConfigDict(env_prefix="ES_")

    hosts: str = "http://localhost:9201"
    username: str = ""
    password: str = ""
    index_prefix: str = "kp"
    verify_certs: bool = False

    @property
    def chunks_index(self) -> str:
        return f"{self.index_prefix}_kb_chunks"


class MinIOSettings(BaseSettings):
    """MinIO — 原始文档存储"""

    model_config = SettingsConfigDict(env_prefix="MINIO_")

    endpoint: str = "localhost:9002"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    bucket: str = "kp-docs"
    secure: bool = False


class KafkaSettings(BaseSettings):
    """Kafka — 异步 ETL 任务队列 + 事件流 + 死信队列"""

    model_config = SettingsConfigDict(env_prefix="KAFKA_")

    bootstrap_servers: str = "localhost:9094"
    ingest_topic: str = "kp.ingest.request"
    result_topic: str = "kp.ingest.result"
    dlq_topic: str = "kp.ingest.dlq"
    consumer_group: str = "kp-worker"
    # 消息重试次数（超过后投递 DLQ）
    max_retries: int = 3


class LangfuseSettings(BaseSettings):
    """Langfuse — LLM 可观测性（抽取成本/延迟/质量追踪）"""

    model_config = SettingsConfigDict(env_prefix="LANGFUSE_")

    enabled: bool = False
    host: str = "http://localhost:3000"
    public_key: str = ""
    secret_key: str = ""


class LLMSettings(BaseSettings):
    """LLM 配置 — 用于结构化抽取（关键词/摘要/实体/FAQ）"""

    model_config = SettingsConfigDict(env_prefix="LLM_")

    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    primary_model: str = "qwen2.5:7b"
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout_seconds: float = 60.0


class RAGSettings(BaseSettings):
    """RAG 检索 + 嵌入 + 重排配置"""

    model_config = SettingsConfigDict(env_prefix="RAG_")

    # 检索参数
    top_k: int = 5
    rerank: bool = True
    rrf_k: int = 60
    confidence_threshold: float = 0.3

    # 嵌入 (TEI BGE-M3)
    embedding_provider: str = "tei"
    embedding_model: str = "BAAI/bge-M3"
    embedding_dim: int = 1024
    embedding_batch_size: int = 128
    tei_base_url: str = "http://localhost:8082"
    embedding_timeout: float = 30.0
    embedding_max_retries: int = 2

    # 重排 (TEI BGE-reranker-v2-m3)
    reranker_provider: str = "tei"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    tei_rerank_base_url: str = "http://localhost:8083"

    # 分块
    chunk_size: int = 1500
    chunk_overlap: int = 200

    # 嵌入版本治理
    embedding_model_version: str = "bge-m3-v1"
    # 影子索引灰度：检索时可指定 model_version 过滤，测试新模型效果
    # 切换流程：1)新模型灌入用新 version → 2)灰度检索验证 → 3)切换默认 version → 4)清理旧
    shadow_model_version: str = ""

    # 入库锁
    ingestion_lock_ttl: int = 600


class SecuritySettings(BaseSettings):
    """安全配置 — 认证 + 敏感词 + 上传校验"""

    model_config = SettingsConfigDict(env_prefix="SECURITY_")

    # API Key 认证（逗号分隔多个 Key）
    api_keys: str = ""
    # 敏感词词典文件路径
    sensitive_words_path: str = ""
    # 文件上传限制
    max_upload_size_mb: int = 50
    allowed_file_extensions: str = ".pdf,.docx,.html,.htm,.md,.markdown,.txt,.xlsx"
    # 限流
    rate_limit_per_minute: int = 60


class Settings(BaseSettings):
    """全局配置根"""

    model_config = SettingsConfigDict(
        env_prefix="KP_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "knowledge-platform"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    service_host: str = "0.0.0.0"
    service_port: int = 8100

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # 子配置
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    elasticsearch: ElasticsearchSettings = Field(default_factory=ElasticsearchSettings)
    minio: MinIOSettings = Field(default_factory=MinIOSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)
    langfuse: LangfuseSettings = Field(default_factory=LangfuseSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)

    @property
    def api_keys_list(self) -> list[str]:
        """解析 API Keys 为列表"""
        return [k.strip() for k in self.security.api_keys.split(",") if k.strip()]

    @property
    def allowed_extensions_list(self) -> set[str]:
        """解析允许的文件扩展名"""
        return {ext.strip().lower() for ext in self.security.allowed_file_extensions.split(",") if ext.strip()}

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.environment == "production":
            if self.log_level == "DEBUG":
                raise ValueError("生产环境不允许 DEBUG 日志级别")
        return self


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()
