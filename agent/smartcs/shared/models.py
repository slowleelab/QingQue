"""共享数据模型

对应概要设计 §2 核心数据模型，定义跨模块复用的 Pydantic 模型。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# ── 枚举类型 ──


class ChannelType(StrEnum):
    WEB = "web"
    APP = "app"
    WECHAT = "wechat"
    PHONE = "phone"


class SessionPhase(StrEnum):
    BOT = "bot"
    AGENT = "agent"
    ENDED = "ended"


class SessionSubPhase(StrEnum):
    """会话子阶段，phase:sub 形式

    BOT 阶段:  bot:active
    AGENT 阶段: agent:queued → agent:assigned → agent:active ⇄ agent:on_hold → agent:reviewing
    ENDED 阶段: 无子阶段，end_reason 记录终止原因
    """

    BOT_ACTIVE = "bot:active"
    AG_QUEUED = "agent:queued"
    AG_ASSIGNED = "agent:assigned"
    AG_ACTIVE = "agent:active"
    AG_ON_HOLD = "agent:on_hold"
    AG_REVIEWING = "agent:reviewing"


class IntentLabel(StrEnum):
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


class SentimentLabel(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    ANGRY = "angry"


class AlertLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertCategory(StrEnum):
    COMPLIANCE = "compliance"
    EMOTION = "emotion"
    SILENCE = "silence"
    PROCESS = "process"


class TransferTriggerLevel(StrEnum):
    L1 = "L1"  # 关键词触发
    L2 = "L2"  # 语义识别
    L3 = "L3"  # 连续低置信度


class DegradationLevel(StrEnum):
    """LLM 降级级别"""
    NORMAL = "normal"        # LLM 可用，正常调用
    DEGRADED = "degraded"    # LLM 降级，跳过 LLM 用检索摘要
    FALLBACK = "fallback"    # LLM 不可用，跳过检索直接用模板


class RiskActionEnum(StrEnum):
    """风控动作"""
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class OEState(StrEnum):
    """编排引擎状态"""
    IDLE = "IDLE"
    EVALUATING = "EVALUATING"
    DISPATCHING = "DISPATCHING"
    WAITING_RESULTS = "WAITING_RESULTS"
    COMPLETED = "COMPLETED"


# ── 状态转换白名单 ──

VALID_TRANSITIONS: dict[tuple[str, str], set[str]] = {
    ("bot", "bot:active"): {"agent:queued", "ended"},
    ("agent", "agent:queued"): {"agent:assigned", "bot:active", "ended"},
    ("agent", "agent:assigned"): {"agent:active", "agent:queued", "ended"},
    ("agent", "agent:active"): {"agent:on_hold", "agent:assigned", "agent:reviewing", "bot:active", "ended"},
    ("agent", "agent:on_hold"): {"agent:active", "ended"},
    ("agent", "agent:reviewing"): {"ended"},
}

_SUB_PHASE_TO_PHASE: dict[SessionSubPhase, SessionPhase] = {
    SessionSubPhase.BOT_ACTIVE: SessionPhase.BOT,
    SessionSubPhase.AG_QUEUED: SessionPhase.AGENT,
    SessionSubPhase.AG_ASSIGNED: SessionPhase.AGENT,
    SessionSubPhase.AG_ACTIVE: SessionPhase.AGENT,
    SessionSubPhase.AG_ON_HOLD: SessionPhase.AGENT,
    SessionSubPhase.AG_REVIEWING: SessionPhase.AGENT,
}


def validate_transition(phase: SessionPhase, sub_phase: SessionSubPhase, target_sub: SessionSubPhase) -> bool:
    """校验状态转换是否合法"""
    key = (phase.value, sub_phase.value)
    allowed = VALID_TRANSITIONS.get(key)
    if allowed is None:
        return False
    return target_sub.value in allowed


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


class EmotionVector(BaseModel):
    """情绪向量（带时间衰减）

    对应设计文档 §3.1 情绪向量衰减公式:
    emotion_vector(t) = emotion_raw × exp(-λ × Δt)
    λ = 0.005 (半衰期约 2.3 分钟, 适配客服对话节奏)
    """
    label: SentimentLabel
    score: float = Field(ge=0.0, le=1.0)
    decay_lambda: float = 0.005
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def decayed_score(self, delta_seconds: float) -> float:
        """计算衰减后的情绪分数"""
        import math
        return self.score * math.exp(-self.decay_lambda * delta_seconds)


class DialogueTurn(BaseModel):
    """对话轮次"""

    turn_id: str
    session_id: str
    speaker: Literal["customer", "agent", "bot"]
    content: str
    emotion_label: SentimentLabel | None = None
    emotion_score: float | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── 会话状态 ──


class SessionState(BaseModel):
    """会话状态对象

    对应概要设计 §2.2，存储在 Redis 中。
    """

    session_id: str
    customer_id: str | None = None
    channel_type: ChannelType = ChannelType.WEB
    current_phase: SessionPhase = SessionPhase.BOT
    sub_phase: SessionSubPhase | None = SessionSubPhase.BOT_ACTIVE
    end_reason: str | None = None  # completed/timeout/cust_disconnect/agent_disconnect/system_error

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
    # 编排层扩展字段（对应设计文档 §3.2）
    intent_stack: list[IntentLabel] = Field(default_factory=list)
    entity_pool: list[Entity] = Field(default_factory=list)
    emotion_vector: EmotionVector | None = None
    suppress_flag: bool = False  # 营销压制标记（单向门 false→true，对应文档 §3.2 覆写规则）
    node_position: str = ""  # LangGraph DAG 节点位置
    risk_pending_audit: bool = False  # 风控待审标记
    transfer_reason: str | None = None
    transfer_summary: str | None = None

    # 元数据
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_active_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    version: int = 1


# ── 知识库元数据 ──


class CategoryEnum(StrEnum):
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
    search_type: Literal["hybrid", "bm25_only", "vector_only"] = "hybrid"
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
    confidence: Literal["high", "medium", "low"] = "medium"


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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trigger: str = ""
    payload: AssistPushPayload = Field(default_factory=AssistPushPayload)


class ExecutorResult(BaseModel):
    """执行器结果"""
    executor_id: str
    ui_schema: dict = Field(default_factory=dict)
    latency_ms: int = 0
    success: bool = True
    degraded: bool = False
    degradation_type: str = ""
    risk_action: RiskActionEnum | None = None
    trace_id: str = ""


class ArbitrationResult(BaseModel):
    """仲裁结果"""
    primary_card: dict | None = None
    risk_badge: dict | None = None
    marketing_slot: dict | None = None
    fusion_type: str = "service_only"
    trace_id: str = ""


class OrchestrationState(BaseModel):
    """编排引擎状态（每次 OE 调度周期的快照）"""
    session_id: str
    oe_state: OEState = OEState.IDLE
    d1_activated: bool = False
    d2_activated: bool = False
    d3_activated: bool = True  # 风控始终激活
    d1_cooldown_remaining: int = 0
    d2_cooldown_remaining: int = 0
    activation_history: list[dict] = Field(default_factory=list)
    global_timeout_ms: int = 5000


class FeedbackSignal(BaseModel):
    """隐式反馈信号

    对应设计文档 §3.6 反馈闭环层:
    - 直接发送 → accept, confidence 1.0
    - 修改后发送 → modify, confidence 0.5
    - 复制部分内容 → partial_accept, confidence 0.3
    - 忽略 → reject, confidence 0.0
    """
    session_id: str
    agent_id: str
    action: Literal["accept", "modify", "partial_accept", "reject"] = "reject"
    confidence: float = 0.0
    modify_fields: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


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


class ChatSendRequest(ChatRequest):
    """客户端发送消息请求（复用 ChatRequest 字段）"""

    pass


class ChatSendResponse(BaseModel):
    """发送消息响应"""

    accepted: bool = True
    message_id: str
    session_id: str


class PollResponse(BaseModel):
    """长轮询响应"""

    has_message: bool = False
    session_id: str = ""
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
    phase: Literal["AGENT", "ENDED", "agent", "ended"]
    sub_phase: str | None = None
    agent_id: str | None = None
    end_reason: str | None = None


class SessionUpdateResponse(BaseModel):
    """会话状态更新响应"""

    status: str = "ok"
