"""全局配置管理

基于 pydantic-settings，支持环境变量覆盖。
所有配置项对应设计文档中的技术选型版本锁定。
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """PostgreSQL 配置"""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_")

    host: str = "localhost"
    port: int = 5432
    user: str = "lumio"
    password: str = "lumio_pass"
    database: str = "lumio"
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
    index_prefix: str = "lumio"
    verify_certs: bool = False


class MilvusSettings(BaseSettings):
    """Milvus 配置"""

    model_config = SettingsConfigDict(env_prefix="MILVUS_")

    host: str = "localhost"
    port: int = 19530
    collection_name: str = "lumio_knowledge"
    vector_dim: int = 1024  # bge-large-zh-v1.5 输出维度


class MinIOSettings(BaseSettings):
    """MinIO 配置"""

    model_config = SettingsConfigDict(env_prefix="MINIO_")

    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    bucket: str = "lumio-docs"
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

    # ── A0: KV Cache 优化配置 ──
    # 三层 cache 控制
    kv_cache_enabled: bool = True
    static_prefix_anchor: bool = True  # L1: 静态前缀锚定
    layered_injection: bool = True  # L2: 状态分层注入
    # 推理引擎: ollama (0% prefix cache) / vllm (PagedAttention) / tgi
    inference_engine: str = "ollama"
    # 长上下文窗口配置 (A7)
    max_context_tokens: int = 8192
    reserved_tokens: int = 1024  # 留给 LLM 输出
    # 分层 token 预算 (A2)
    budget_static: int = 800  # 稳态层
    budget_customer: int = 400  # 半稳态层 (客户画像)
    budget_rag: int = 1200  # 动态层 (RAG 检索)
    budget_history: int = 1500  # 动态层 (历史)
    budget_current: int = 200  # 动态层 (当前轮)


class CompressionSettings(BaseSettings):
    """A1: 上下文压缩配置"""

    model_config = SettingsConfigDict(env_prefix="COMPRESS_")

    # 算法: llmlingua / selective / semantic
    algorithm: str = "selective"
    # 目标压缩比 (original/compressed), e.g. 5.0 表示压到 1/5
    target_ratio: float = 4.0
    # 质量下限 (1-5, 低于此值回退到原文)
    min_quality_score: float = 3.5
    # 启用开关 (灰度用)
    enabled: bool = True
    # Selective Context: 保留多少信息量
    preserve_ratio: float = 0.35
    # 触发压缩的历史长度阈值 (轮数)
    min_history_turns: int = 8


class BudgetSettings(BaseSettings):
    """E0: LLM Token 成本预算配置"""

    model_config = SettingsConfigDict(env_prefix="BUDGET_")

    # 全局月度预算（美元）
    monthly_budget_usd: float = 5000.0
    # 单租户日预算（美元）
    per_tenant_daily_limit_usd: float = 100.0
    # 模型单价 (USD per 1M tokens)
    cost_per_1m_input_tokens: dict[str, float] = Field(
        default_factory=lambda: {
            "qwen2.5:7b": 0.18,
            "qwen2.5:72b": 1.80,
            "deepseek-v3": 0.27,
            "gpt-4o": 5.00,
        }
    )
    cost_per_1m_output_tokens: dict[str, float] = Field(
        default_factory=lambda: {
            "qwen2.5:7b": 0.18,
            "qwen2.5:72b": 1.80,
            "deepseek-v3": 1.10,
            "gpt-4o": 15.00,
        }
    )


class GuardrailSettings(BaseSettings):
    """A3-A5: 提示词注入防御配置"""

    model_config = SettingsConfigDict(env_prefix="GUARD_")

    # User Input 层 (A3)
    user_input_enabled: bool = True
    user_input_layer1_regex: bool = True
    user_input_layer2_role_confusion: bool = True
    user_input_layer3_guard_llm: bool = False  # 默认关, 灰度开
    # RAG 检索内容层 (A4)
    rag_content_sanitizer_enabled: bool = True
    rag_suspicious_threshold: float = 0.6
    # Tool 返回结果层 (A5)
    tool_result_sanitizer_enabled: bool = True
    tool_result_max_size_bytes: int = 4096
    # 实体池白名单 (A6) - 仅这些 entity_type 可跨会话带入
    # P2-1: 双源一致性 - 同步 lumio.shared.entity_sandbox.DEFAULT_ENTITY_ALLOWLIST
    # 之前 entity_sandbox 有 7 个默认白名单, config 仅有 5 个, 不同步会导致环境变量未覆盖时
    # config 实际生效白名单仅 5 个, 但 entity_sandbox 代码侧按 7 个处理, 行为不一致.
    entity_pool_allowlist: list[str] = Field(
        default_factory=lambda: [
            "card_type",  # 卡种 (VISA/银联)
            "vip_level",  # VIP 等级
            "risk_tolerance",  # 风险偏好 R1-R4
            "city",  # 城市
            "occupation",  # 职业
            "age_range",  # 年龄段
            "product_interest",  # 产品兴趣
        ]
    )


class PromptSettings(BaseSettings):
    """B0: 提示词注册中心配置"""

    model_config = SettingsConfigDict(env_prefix="PROMPT_")

    # Nacos 配置中心地址
    nacos_server_addr: str = "localhost:8848"
    nacos_namespace: str = "lumio"
    # Redis 缓存 TTL
    cache_ttl_seconds: int = 60
    # 本地兜底: 离线时使用 prompts.py 硬编码常量
    fallback_to_local: bool = True
    # 启动时打印当前生效版本
    log_active_version: bool = True


class ExperimentSettings(BaseSettings):
    """B5: A/B 实验框架配置"""

    model_config = SettingsConfigDict(env_prefix="EXPERIMENT_")

    # 粘性分桶开关
    enabled: bool = True
    # Redis 持久化 key TTL (秒)
    bucket_ttl_seconds: int = 86400 * 30  # 30 天
    # 最小样本量 (统计显著性检验)
    min_sample_size: int = 1000
    # 显著性水平 (p-value 阈值)
    p_value_threshold: float = 0.05


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
    bot_idle_timeout: int = 180  # BOT 阶段空闲超时 (银行客户可能边查资料边聊, 2 分钟易误杀)
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
    dismiss_extend_ratio: float = 2.0  # 坐席关闭→间隔×2.0
    ignore_extend_ratio: float = 1.5  # 连续忽略3次→间隔×1.5


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


class ObservabilitySettings(BaseSettings):
    """可观测性配置：链路追踪与 OTLP 导出

    命名空间: 主用 ``OBSERVABILITY_*`` (项目统一风格)，同时支持旧名
    ``LUMIO_TRACING_ENABLED`` / ``JAEGER_HOST`` (向后兼容 conftest / docker-compose
    已注入的环境变量，避免破坏现有部署).
    """

    model_config = SettingsConfigDict(
        env_prefix="OBSERVABILITY_",
        extra="ignore",
    )

    # Python 链路追踪总开关. AliasChoices 保证旧 LUMIO_TRACING_ENABLED 仍生效.
    tracing_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "OBSERVABILITY_TRACING_ENABLED",
            "LUMIO_TRACING_ENABLED",
        ),
    )
    # OTLP 追踪后端主机. 旧 JAEGER_HOST 仍生效.
    jaeger_host: str = Field(
        default="localhost",
        validation_alias=AliasChoices(
            "OBSERVABILITY_JAEGER_HOST",
            "JAEGER_HOST",
        ),
    )
    # 可选: 显式 OTLP endpoint (含 path). 为空时按 jaeger_host 拼
    # http://{jaeger_host}:4318/v1/traces
    otlp_endpoint: str | None = None
    # 追踪采样率 (0.0~1.0). 默认 1.0 (全采样, dev 友好).
    # prod 建议 0.1; 用 ParentBasedTraceIdRatioSampler 包装保证跨服务 trace 不被切断.
    sampling_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "OBSERVABILITY_SAMPLING_RATIO",
            "LUMIO_TRACING_SAMPLE",  # 与 Java 端 MCP_TRACING_SAMPLE 命名习惯一致
        ),
    )


class MCPBackend(BaseModel):
    """路由模式下的单个 MCP 后端定义

    用于「多 server」拓扑：host（MCPToolClient）连接多个后端，合并各自工具目录，
    按域前缀命名空间避免撞名，并按名字分发调用。默认 ``MCPSettings.backends=[]`` →
    退回单后端（``endpoint``、``prefix=""``），名字与 schema 契约不变（零回归）。
    """

    # 后端逻辑名（用于分发索引与日志，不进入 host-facing 工具名）
    name: str
    # 后端 MCP 入口 URL（streamable-http）
    endpoint: str
    # host-facing 工具名前缀（域命名空间，如 "card."）；单后端留空即原名
    prefix: str = ""
    # 该后端的敏感工具原名白名单（与工具注解、全局 sensitive_tools 取并集）
    sensitive_tools: list[str] = Field(default_factory=list)


class MCPSettings(BaseSettings):
    """MCP 工具层配置（经 Higress AI 网关调用受治理工具）

    默认 ``enabled=False``：编排大脑行为与现状完全一致（零回归）。
    """

    model_config = SettingsConfigDict(env_prefix="MCP_")

    # 总开关：关闭时不加载任何工具，bot 走原有 RAG/LLM 生成路径
    enabled: bool = False
    # Higress MCP 入口 URL（经 Nacos MCP Registry 发现的工具经此暴露）。
    # 默认指向 Higress AI 网关统一 MCP 入口（streamable-http）；本地联调可改指参考 Server（:8080/mcp）。
    endpoint: str = "http://localhost:10000/mcp/credit-card"
    # 工具调用超时（秒）
    timeout_seconds: float = 10.0
    # ── 路由模式：多 MCP 后端（host 侧合并 + 分发）──
    # 默认空 → 单后端（用 endpoint、空前缀、名字契约不变，零回归）。
    # 非空时，host 连接每个后端、按 prefix 生成域命名空间工具名并合并目录，
    # 按 name→(server, raw_name) 分发调用；某后端失败仅其工具缺席，不整体报错。
    # 可经环境变量 MCP_BACKENDS 以 JSON 数组配置。
    backends: list[MCPBackend] = Field(default_factory=list)
    # 敏感工具白名单（与工具自身注解取并集，命中即需用户确认）
    sensitive_tools: list[str] = Field(default_factory=list)
    # 工具调用循环最大轮数（防死循环）
    max_tool_iterations: int = 5
    # 待确认操作（pending_action）过期时间（秒）
    confirmation_ttl_seconds: int = 300
    # 确认窗口内无法判定（unclear）连续次数上限, 达到后自动取消 pending 并放行新消息
    # (业务: 用户确认期间发新问题不能被无限吞掉, 需逃生路径)
    unclear_auto_cancel_threshold: int = 3

    # ── 工具护栏（Python 侧纵深防御，与 Higress 治理互补）──
    # 按角色的工具授权白名单：{role: [tool, ...]}。
    # 默认空 → 不做授权限制（零回归）；一旦配置非空，未列出的角色/工具将被拒绝。
    tool_role_allowlist: dict[str, list[str]] = Field(default_factory=dict)
    # 敏感工具入参额度上限：{tool_name: max_amount}。默认空 → 不做额度校验。
    tool_amount_limits: dict[str, float] = Field(default_factory=dict)
    # 视为「金额」的入参键名，命中任一且超过对应上限即拒绝。
    amount_arg_keys: list[str] = Field(default_factory=lambda: ["amount", "target_limit", "target_amount", "limit"])

    # ── 渐进式工具暴露（Progressive Disclosure）──
    # 开关：默认关 → bot 业务路由与工具暴露完全同现状（零回归）。
    # 开启后，命中「查询类工具意图」且置信度达标时，仅向 LLM 暴露该意图对应的工具子集，
    # 降低多工具（22+）场景下的选择噪声与 token 开销；未命中/低置信时回落全量或知识问答。
    progressive_disclosure_enabled: bool = False
    # 渐进式暴露的置信度阈值：意图置信度 >= 此值才裁剪到子集，否则暴露全量交 LLM 判断。
    pd_confidence_threshold: float = 0.7
    # 意图 → 工具子集映射：{intent_label: [tool_name, ...]}。
    # 键为 IntentLabel 值（如 "bill_query"）；值为工具名（单后端时即工具原名，
    # 多后端路由模式下为带域前缀全名，见 MCPToolClient）。默认给出五类查询意图的常用子集。
    intent_tool_map: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "bill_query": ["query_card_bill", "query_bill_detail", "query_annual_fee", "repay_credit_card"],
            "transaction_query": ["query_transactions", "report_transaction_dispute"],
            "limit_query": [
                "query_credit_limit",
                "query_limit_adjust_history",
                "adjust_temp_credit_limit",
                "apply_permanent_limit",
            ],
            "installment_inquiry": [
                "query_installment_offer",
                "query_installment_status",
                "apply_bill_installment",
                "cancel_installment",
            ],
            "reward_query": ["query_points", "query_card_benefits", "redeem_points"],
        }
    )


class Settings(BaseSettings):
    """全局配置根"""

    model_config = SettingsConfigDict(
        env_prefix="LUMIO_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务标识
    service_name: str = "lumio"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    service_host: str = "127.0.0.1"

    # P0-3 整改: dev 旁路认证 (无 token 直接返回 admin) 必须显式开启
    # 默认 False — dev 环境不自动放行, 避免 0.0.0.0 绑定 + 漏配 LUMIO_ENVIRONMENT
    # 导致远端部署裸奔. 显式设 LUMIO_DEV_AUTH_BYPASS=true 才启用.
    dev_auth_bypass: bool = False

    # chat-svc 客户端
    chat_svc_url: str = "http://localhost:8080"

    # CORS
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # 认证（JWT）
    jwt_secret: str = "lumio-dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30  # access token 30 分钟
    jwt_refresh_expire_days: int = 7  # refresh token 7 天

    @model_validator(mode="after")
    def _validate_production_security(self) -> Settings:
        """生产环境安全检查: 拒绝使用默认 JWT 密钥 / 弱密钥启动.

        任何环境都禁止使用占位密钥 ('lumio-dev-secret-change-in-production')
        和 <CHANGE_ME> 占位符 — 这是 P0-2 整改: 漏配 LUMIO_ENVIRONMENT
        不再让默认密钥静默通过.
        """
        # 永远禁止: 占位密钥 (历史 dev 默认 + P0-1 新占位符)
        forbidden_secrets = {
            "lumio-dev-secret-change-in-production",
            "<CHANGE_ME>",
            "<CHANGE_ME_IF_NEEDED>",
        }
        if self.jwt_secret in forbidden_secrets:
            if self.environment == "production":
                raise ValueError("生产环境必须设置 LUMIO_JWT_SECRET 环境变量, " f"禁止占位密钥 ({self.jwt_secret!r})")
            # dev/test 也不放过, 改 WARNING, 不阻断启动 (兼容现有 dev flow)
            import logging

            logging.getLogger(__name__).warning(
                "⚠️  JWT 密钥仍为占位值 %r, 部署到任何对外环境前必改. "
                "建议: export LUMIO_JWT_SECRET=$(openssl rand -hex 32)",
                self.jwt_secret,
            )
        # 长度检查仅在生产强制
        if self.environment == "production" and self.jwt_secret and len(self.jwt_secret) < 32:
            raise ValueError("生产环境 JWT 密钥长度必须 >= 32 字符")
        # P0-5 第三轮修复: 生产环境外部服务凭据默认值强制校验
        # 此前仅校验 JWT, LLM API key 默认 "ollama" / MinIO minioadmin 漏配直接可用.
        if self.environment == "production":
            if self.llm.api_key in ("", "ollama"):
                raise ValueError(
                    "生产环境必须设置 LLM_API_KEY (默认占位 'ollama' 禁止在生产使用, " "漏配会用假凭证直连外部模型服务)"
                )
            if self.minio.access_key in ("", "minioadmin") or self.minio.secret_key in ("", "minioadmin"):
                raise ValueError("生产环境必须设置 MINIO_ACCESS_KEY / MINIO_SECRET_KEY (禁止默认 minioadmin)")
            if not self.elasticsearch.username or not self.elasticsearch.password:
                raise ValueError("生产环境必须设置 ES_USERNAME / ES_PASSWORD (禁止匿名连接)")
            if not self.redis.password:
                raise ValueError("生产环境必须设置 REDIS_PASSWORD (禁止无密码 Redis)")
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
    circuit_breaker: CircuitBreakerConfigSettings = Field(default_factory=CircuitBreakerConfigSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    # Sprint A+ P0 增强配置
    compression: CompressionSettings = Field(default_factory=CompressionSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    guard: GuardrailSettings = Field(default_factory=GuardrailSettings)
    prompt: PromptSettings = Field(default_factory=PromptSettings)
    experiment: ExperimentSettings = Field(default_factory=ExperimentSettings)


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()
