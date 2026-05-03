"""共享数据模型

对应概要设计 §2 核心数据模型，定义跨模块复用的 Pydantic 模型。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# ── 枚举类型 ──


class ChannelType(str, Enum):
    WEB = "web"
    APP = "app"
    WECHAT = "wechat"
    PHONE = "phone"


class SessionPhase(str, Enum):
    BOT = "bot"
    HANDOFF = "handoff"
    ASSIST = "assist"
    ENDED = "ended"


class IntentLabel(str, Enum):
    """意图标签，覆盖主要信用卡业务场景"""

    FAQ = "faq"
    BILL_QUERY = "bill_query"
    TRANSACTION_QUERY = "transaction_query"
    LIMIT_QUERY = "limit_query"
    INSTALLMENT_INQUIRY = "installment_inquiry"
    REWARD_QUERY = "reward_query"
    CARD_LOSS = "card_loss"
    COMPLAINT = "complaint"
    TRANSFER_AGENT = "transfer_agent"
    CHITCHAT = "chitchat"


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    ANGRY = "angry"


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertCategory(str, Enum):
    COMPLIANCE = "compliance"
    EMOTION = "emotion"
    SILENCE = "silence"
    PROCESS = "process"


class TransferTriggerLevel(str, Enum):
    L1 = "L1"  # 关键词触发
    L2 = "L2"  # 语义识别
    L3 = "L3"  # 连续低置信度


class DegradationLevel(str, Enum):
    """LLM 降级级别"""
    NORMAL = "normal"        # LLM 可用，正常调用
    DEGRADED = "degraded"    # LLM 降级，跳过 LLM 用检索摘要
    FALLBACK = "fallback"    # LLM 不可用，跳过检索直接用模板


# ── 基础数据结构 ──


class Entity(BaseModel):
    """抽取的实体"""

    entity_type: str
    value: str
    start: int | None = None
    end: int | None = None
    confidence: float = 1.0


class IntentResult(BaseModel):
    """意图分类结果"""

    primary_intent: IntentLabel
    primary_confidence: float
    alternatives: list[IntentLabel] = Field(default_factory=list)


class SentimentResult(BaseModel):
    """情感分析结果"""

    label: SentimentLabel
    score: float


class DialogueTurn(BaseModel):
    """对话轮次"""

    turn_id: str
    session_id: str
    speaker: str  # "customer" | "agent" | "bot"
    content: str
    emotion_label: SentimentLabel | None = None
    emotion_score: float | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


# ── 会话状态 ──


class SessionState(BaseModel):
    """会话状态对象

    对应概要设计 §2.2，存储在 Redis 中。
    """

    session_id: str
    customer_id: str | None = None
    channel_type: ChannelType = ChannelType.WEB
    current_phase: SessionPhase = SessionPhase.BOT

    # 客户画像
    vip_level: str = "普通"
    card_types: list[str] = Field(default_factory=list)
    risk_tolerance: str = "R2"

    # 对话历史
    turns: list[DialogueTurn] = Field(default_factory=list)
    turn_count: int = 0

    # 机器人阶段状态
    last_intent: IntentLabel | None = None
    last_entities: list[Entity] = Field(default_factory=list)
    confidence_history: list[float] = Field(default_factory=list)
    low_confidence_streak: int = 0
    human_request_score: int = 0

    # 坐席阶段状态
    agent_id: str | None = None
    transfer_reason: str | None = None
    transfer_summary: str | None = None

    # 元数据
    created_at: datetime = Field(default_factory=datetime.now)
    last_active_at: datetime = Field(default_factory=datetime.now)
    version: int = 1


# ── 知识库元数据 ──


class CategoryEnum(str, Enum):
    """知识文档业务分类"""

    FAQ = "FAQ"
    FEE = "费率"
    POINTS = "积分"
    ANNUAL_FEE = "年费"
    REGULATIONS = "章程"
    REPAYMENT = "还款"
    SECURITY = "安全"
    ACTIVITY = "活动"
    OTHER = "OTHER"


class DocumentMetadata(BaseModel):
    """文档元数据，入库管道使用"""

    doc_id: str
    category: str
    doc_type: str
    keywords: list[str] = Field(default_factory=list)
    card_type: str | None = None
    customer_tier: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None
    security_level: str = "internal"
    version: str = "1.0"


class RerankResult(BaseModel):
    """重排序结果"""

    index: int
    relevance_score: float
    text: str


# ── 检索结果 ──


class RetrievedChunk(BaseModel):
    """检索到的知识块"""

    chunk_id: str
    content: str
    score: float
    source_doc: str
    metadata: dict = Field(default_factory=dict)


class RetrieveRequest(BaseModel):
    """检索请求"""

    query: str
    top_k: int = 5
    filters: dict = Field(default_factory=dict)
    rerank: bool = True
    search_type: str = "hybrid"  # hybrid / bm25_only / vector_only
    rrf_k: int | None = None  # 覆盖 RRF k 参数；None 时使用配置默认值


class RetrieveResponse(BaseModel):
    """检索响应"""

    results: list[RetrievedChunk] = Field(default_factory=list)
    total_candidates: int = 0
    latency_ms: int = 0


# ── 坐席辅助推送 ──


class ScriptCard(BaseModel):
    """话术卡片"""

    script_id: str
    content: str
    tags: list[str] = Field(default_factory=list)
    priority: int = 1


class KnowledgeSnippet(BaseModel):
    """知识片段"""

    chunk_id: str
    summary: str
    source: str
    confidence: str = "medium"  # high / medium / low


class AlertObject(BaseModel):
    """告警对象"""

    level: AlertLevel
    category: AlertCategory
    message: str
    suggestion: str = ""


class ProductRecommendation(BaseModel):
    """产品推荐"""

    product_id: str
    product_name: str
    reason: str
    script_suggestion: str
    risk_tip: str
    eligibility_match: bool = True


class AssistPushPayload(BaseModel):
    """坐席辅助推送载荷"""

    scripts: list[ScriptCard] = Field(default_factory=list)
    knowledge: list[KnowledgeSnippet] = Field(default_factory=list)
    alerts: list[AlertObject] = Field(default_factory=list)
    recommendations: list[ProductRecommendation] = Field(default_factory=list)


class AssistPushMessage(BaseModel):
    """坐席辅助推送消息"""

    type: str = "assist_push"
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    trigger: str = ""
    payload: AssistPushPayload = Field(default_factory=AssistPushPayload)


# ── 话后小结 ──


class CallSummary(BaseModel):
    """话后小结"""

    summary_id: str
    session_id: str
    customer_demand: str = ""
    problem_category: str = ""
    solution_provided: str = ""
    resolution_status: str = ""
    key_info: dict = Field(default_factory=dict)
    sentiment: SentimentLabel = SentimentLabel.NEUTRAL
    confidence: float = 0.0


# ── API 请求/响应 ──


class ChatRequest(BaseModel):
    """机器人聊天请求"""

    session_id: str | None = None
    customer_id: str | None = None
    message: str
    channel: ChannelType = ChannelType.WEB


class ChatResponse(BaseModel):
    """机器人聊天响应"""

    session_id: str
    reply: str
    intent: IntentLabel | None = None
    confidence: float = 0.0
    source: str = "rag"  # rag / fallback / bank_api
    is_transfer: bool = False


# ── 长轮询 ──


class ChatSendRequest(BaseModel):
    """客户端发送消息请求"""

    session_id: str | None = None
    customer_id: str | None = None
    message: str
    channel: ChannelType = ChannelType.WEB


class ChatSendResponse(BaseModel):
    """发送消息响应"""

    accepted: bool = True
    message_id: str
    session_id: str


class PollResponse(BaseModel):
    """长轮询响应"""

    has_message: bool = False
    reply: str = ""
    intent: IntentLabel | None = None
    confidence: float = 0.0
    source: str = "rag"
    is_transfer: bool = False
    transfer_url: str = ""
    transfer_reason: str = ""


class SessionUpdateRequest(BaseModel):
    """会话状态更新请求（star-connection 回调）"""

    session_id: str
    phase: str  # "ASSIST" | "ENDED"
    agent_id: str | None = None


class SessionUpdateResponse(BaseModel):
    """会话状态更新响应"""

    status: str = "ok"
