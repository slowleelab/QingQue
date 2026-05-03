"""LangGraph Agent 编排图

基于 StateGraph 实现 Supervisor 路由模式：
classify_intent → supervisor → {knowledge_agent, business_agent, fallback_agent}
                → transfer_check → {respond, handoff}

状态通过 LangGraph checkpoint 自动持久化。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, StateGraph

from smartcs.shared.config import get_settings
from smartcs.shared.models import (
    DialogueTurn,
    Entity,
    IntentLabel,
    IntentResult,
    RetrieveRequest,
    RetrieveResponse,
    SentimentLabel,
    SessionPhase,
    SessionState,
)

from smartcs.services.bot.prompts import (
    BUSINESS_SYSTEM_PROMPT,
    BUSINESS_TRANSFER_TEMPLATE,
    FALLBACK_SYSTEM_PROMPT,
    FAREWELL_RESPONSE,
    GREETING_RESPONSE,
    KNOWLEDGE_SYSTEM_PROMPT,
)
from smartcs.services.common.classifier import IntentClassifier, get_domain
from smartcs.services.common.degradation import DegradationManager
from smartcs.services.common.transfer import TransferChecker

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch
    from pymilvus import Collection

    from smartcs.services.common.embedding import EmbeddingCircuitBreaker
    from smartcs.services.common.session import SessionManager

logger = logging.getLogger(__name__)


# ── Graph State 定义 ──


class AgentState(dict):
    """LangGraph Agent 状态

    所有节点共享的状态字典，通过 LangGraph checkpoint 持久化。
    """

    # 会话标识
    session_id: str
    # 用户输入
    user_input: str
    # 意图分类结果
    intent: IntentResult | None
    entities: list[Entity]
    sentiment: SentimentLabel
    classify_source: str  # "rule" | "llm" | "fallback"
    # 意图域（knowledge / business / fallback）
    domain: str
    # 检索上下文
    retrieval_context: str
    # 生成回复
    response: str
    response_source: str  # "llm" | "retrieval" | "template" | "fallback"
    # 转人工
    should_transfer: bool
    transfer_reason: str
    # 会话状态（用于 L3 累计判断）
    session_state: SessionState | None


def _initial_state(session_id: str, user_input: str) -> AgentState:
    """创建初始状态"""
    return AgentState(
        session_id=session_id,
        user_input=user_input,
        intent=None,
        entities=[],
        sentiment=SentimentLabel.NEUTRAL,
        classify_source="",
        domain="fallback",
        retrieval_context="",
        response="",
        response_source="",
        should_transfer=False,
        transfer_reason="",
        session_state=None,
    )


# ── 节点函数 ──


async def classify_intent_node(state: AgentState, *, classifier: IntentClassifier) -> AgentState:
    """意图分类节点：双通道 Fast/Slow 分类"""
    intent_result, entities, sentiment, source = await classifier.classify(state["user_input"])
    domain = get_domain(intent_result.primary_intent)

    state["intent"] = intent_result
    state["entities"] = entities
    state["sentiment"] = sentiment
    state["classify_source"] = source
    state["domain"] = domain

    logger.info(
        "意图分类: intent=%s, confidence=%.2f, domain=%s, source=%s",
        intent_result.primary_intent.value,
        intent_result.primary_confidence,
        domain,
        source,
    )
    return state


async def supervisor_node(state: AgentState) -> AgentState:
    """路由决策节点：根据意图域选择 Agent"""
    domain = state["domain"]
    logger.debug("Supervisor 路由: domain=%s", domain)
    return state


def supervisor_router(state: AgentState) -> str:
    """Supervisor 条件边：根据 domain 路由到对应 Agent"""
    domain = state.get("domain", "fallback")
    if domain == "knowledge":
        return "knowledge_agent"
    if domain == "business":
        return "business_agent"
    return "fallback_agent"


async def knowledge_agent_node(
    state: AgentState,
    *,
    degradation_mgr: DegradationManager,
    es_client: AsyncElasticsearch | None,
    milvus_collection: Collection | None,
    embedding_breaker: EmbeddingCircuitBreaker | None,
) -> AgentState:
    """知识问答 Agent：RAG 检索 + 降级管理器生成"""
    from smartcs.services.common.retrieval import retrieve as do_retrieve
    from smartcs.shared.models import DegradationLevel

    settings = get_settings()
    user_input = state["user_input"]
    intent = state.get("intent")

    # 检索（FALLBACK 跳过）
    context = ""
    if degradation_mgr.level != DegradationLevel.FALLBACK:
        embedding_provider = embedding_breaker.provider if embedding_breaker and embedding_breaker.is_available else None
        retrieve_response: RetrieveResponse = await do_retrieve(
            request=RetrieveRequest(query=user_input, top_k=settings.rag.top_k, rerank=False),
            es_client=es_client,
            milvus_collection=milvus_collection,
            embedding_provider=embedding_provider,
            reranker=None,
        )
        if retrieve_response.results:
            context_parts = [f"[{i+1}] {r.content}" for i, r in enumerate(retrieve_response.results)]
            context = "\n\n".join(context_parts)
            state["retrieval_context"] = context
        else:
            state["retrieval_context"] = ""
    else:
        state["retrieval_context"] = ""

    # 通过降级管理器生成
    result = await degradation_mgr.generate_with_fallback(
        system_prompt=KNOWLEDGE_SYSTEM_PROMPT,
        user_input=user_input,
        context=context,
        intent_label=intent.primary_intent if intent else None,
    )
    state["response"] = result.content
    state["response_source"] = result.source
    return state


async def business_agent_node(
    state: AgentState,
    *,
    degradation_mgr: DegradationManager,
) -> AgentState:
    """业务办理 Agent：实体填充 + 转人工判断 + 降级管理器生成"""
    user_input = state["user_input"]
    intent = state.get("intent")

    # 挂失/投诉/转人工 → 直接触发转人工
    if intent and intent.primary_intent in (IntentLabel.CARD_LOSS, IntentLabel.COMPLAINT, IntentLabel.TRANSFER_AGENT):
        reason = {
            IntentLabel.CARD_LOSS: "挂失业务",
            IntentLabel.COMPLAINT: "投诉处理",
            IntentLabel.TRANSFER_AGENT: "客户主动请求",
        }.get(intent.primary_intent, "业务办理")
        state["should_transfer"] = True
        state["transfer_reason"] = reason
        state["response"] = BUSINESS_TRANSFER_TEMPLATE.format(reason=reason)
        state["response_source"] = "template"
        return state

    # 其他业务咨询通过降级管理器生成
    result = await degradation_mgr.generate_with_fallback(
        system_prompt=BUSINESS_SYSTEM_PROMPT,
        user_input=user_input,
        context="",
        intent_label=intent.primary_intent if intent else None,
    )
    state["response"] = result.content
    state["response_source"] = result.source
    return state


async def fallback_agent_node(
    state: AgentState,
    *,
    degradation_mgr: DegradationManager,
) -> AgentState:
    """闲聊/兜底 Agent：快速匹配 + 降级管理器生成"""
    user_input = state["user_input"]

    # 快速匹配闲聊模式（不调 LLM）
    if _is_greeting(user_input):
        state["response"] = GREETING_RESPONSE
        state["response_source"] = "template"
        return state

    if _is_farewell(user_input):
        state["response"] = FAREWELL_RESPONSE
        state["response_source"] = "template"
        return state

    # 通过降级管理器生成
    result = await degradation_mgr.generate_with_fallback(
        system_prompt=FALLBACK_SYSTEM_PROMPT,
        user_input=user_input,
        context="",
        intent_label=IntentLabel.CHITCHAT,
    )
    state["response"] = result.content
    state["response_source"] = result.source
    return state


async def transfer_check_node(
    state: AgentState,
    *,
    transfer_checker: TransferChecker,
    session_manager: SessionManager,
) -> AgentState:
    """转人工判断节点"""
    # 如果业务 Agent 已经标记转人工，直接通过
    if state["should_transfer"]:
        return state

    # 加载会话状态用于 L3 判断
    session_state = await session_manager.get_session(state["session_id"])

    intent = state.get("intent") or IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.0)
    should_transfer, level, reason = transfer_checker.check(
        text=state["user_input"],
        intent=intent,
        sentiment=state.get("sentiment", SentimentLabel.NEUTRAL),
        session=session_state,
    )

    state["should_transfer"] = should_transfer
    state["transfer_reason"] = reason

    if should_transfer:
        logger.info("转人工触发: level=%s, reason=%s", level, reason)

    return state


def transfer_router(state: AgentState) -> str:
    """转人工条件边"""
    if state.get("should_transfer"):
        return "handoff"
    return "respond"


async def respond_node(
    state: AgentState,
    *,
    session_manager: SessionManager,
) -> AgentState:
    """回复生成节点：格式化输出 + 写入对话历史"""
    # 记录本轮对话
    turn = DialogueTurn(
        turn_id=uuid.uuid4().hex,
        session_id=state["session_id"],
        speaker="bot",
        content=state["response"],
        timestamp=datetime.now(),
    )

    intent = state.get("intent")
    try:
        await session_manager.add_turn(
            state["session_id"],
            turn,
            intent=intent,
        )
    except Exception:
        logger.warning("写入对话历史失败: session_id=%s", state["session_id"])

    return state


async def handoff_node(
    state: AgentState,
    *,
    session_manager: SessionManager,
) -> AgentState:
    """转人工节点：更新会话阶段 + 构造转接消息"""
    reason = state.get("transfer_reason", "")

    try:
        await session_manager.transition_phase(
            state["session_id"],
            SessionPhase.HANDOFF,
            reason=reason,
        )
    except Exception:
        logger.warning("会话阶段切换失败: session_id=%s", state["session_id"])

    # 如果业务 Agent 已构造好转接消息，使用它
    if not state["response"] or "正在为您转接" not in state["response"]:
        state["response"] = BUSINESS_TRANSFER_TEMPLATE.format(reason=reason)

    # 记录本轮对话
    turn = DialogueTurn(
        turn_id=uuid.uuid4().hex,
        session_id=state["session_id"],
        speaker="bot",
        content=state["response"],
        timestamp=datetime.now(),
    )
    try:
        await session_manager.add_turn(state["session_id"], turn)
    except Exception:
        logger.warning("写入对话历史失败: session_id=%s", state["session_id"])

    return state


# ── 图构建 ──


class SmartCSAgent:
    """SmartCS 对话 Agent

    封装 LangGraph StateGraph，提供简洁的调用接口。
    """

    def __init__(
        self,
        classifier: IntentClassifier,
        degradation_mgr: DegradationManager,
        transfer_checker: TransferChecker,
        session_manager: SessionManager,
        es_client: AsyncElasticsearch | None = None,
        milvus_collection: Collection | None = None,
        embedding_breaker: EmbeddingCircuitBreaker | None = None,
    ) -> None:
        self._classifier = classifier
        self._degradation_mgr = degradation_mgr
        self._transfer_checker = transfer_checker
        self._session_manager = session_manager
        self._es_client = es_client
        self._milvus_collection = milvus_collection
        self._embedding_breaker = embedding_breaker
        self._graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """构建 LangGraph 状态图"""
        graph = StateGraph(AgentState)

        # 添加节点
        graph.add_node("classify_intent", self._classify_intent)
        graph.add_node("supervisor", self._supervisor)
        graph.add_node("knowledge_agent", self._knowledge_agent)
        graph.add_node("business_agent", self._business_agent)
        graph.add_node("fallback_agent", self._fallback_agent)
        graph.add_node("transfer_check", self._transfer_check)
        graph.add_node("respond", self._respond)
        graph.add_node("handoff", self._handoff)

        # 定义边
        graph.set_entry_point("classify_intent")
        graph.add_edge("classify_intent", "supervisor")

        # Supervisor 条件路由
        graph.add_conditional_edges("supervisor", supervisor_router, {
            "knowledge_agent": "knowledge_agent",
            "business_agent": "business_agent",
            "fallback_agent": "fallback_agent",
        })

        # Agent → 转人工检查
        graph.add_edge("knowledge_agent", "transfer_check")
        graph.add_edge("business_agent", "transfer_check")
        graph.add_edge("fallback_agent", "transfer_check")

        # 转人工检查条件路由
        graph.add_conditional_edges("transfer_check", transfer_router, {
            "respond": "respond",
            "handoff": "handoff",
        })

        # 终止
        graph.add_edge("respond", END)
        graph.add_edge("handoff", END)

        return graph.compile()

    async def run(self, session_id: str, user_input: str) -> AgentState:
        """运行 Agent 图

        Args:
            session_id: 会话 ID
            user_input: 用户输入

        Returns:
            最终 Agent 状态
        """
        initial = _initial_state(session_id, user_input)
        result = await self._graph.ainvoke(initial)
        return AgentState(result)

    # ── 节点绑定方法（注入依赖） ──

    async def _classify_intent(self, state: AgentState) -> AgentState:
        return await classify_intent_node(state, classifier=self._classifier)

    async def _supervisor(self, state: AgentState) -> AgentState:
        return await supervisor_node(state)

    async def _knowledge_agent(self, state: AgentState) -> AgentState:
        return await knowledge_agent_node(
            state,
            degradation_mgr=self._degradation_mgr,
            es_client=self._es_client,
            milvus_collection=self._milvus_collection,
            embedding_breaker=self._embedding_breaker,
        )

    async def _business_agent(self, state: AgentState) -> AgentState:
        return await business_agent_node(state, degradation_mgr=self._degradation_mgr)

    async def _fallback_agent(self, state: AgentState) -> AgentState:
        return await fallback_agent_node(state, degradation_mgr=self._degradation_mgr)

    async def _transfer_check(self, state: AgentState) -> AgentState:
        return await transfer_check_node(
            state,
            transfer_checker=self._transfer_checker,
            session_manager=self._session_manager,
        )

    async def _respond(self, state: AgentState) -> AgentState:
        return await respond_node(state, session_manager=self._session_manager)

    async def _handoff(self, state: AgentState) -> AgentState:
        return await handoff_node(state, session_manager=self._session_manager)


# ── 辅助函数 ──


def _is_greeting(text: str) -> bool:
    """判断是否为问候语"""
    greetings = {"你好", "您好", "嗨", "hi", "hello", "在吗", "在不在"}
    return text.strip().lower() in greetings


def _is_farewell(text: str) -> bool:
    """判断是否为告别语"""
    farewells = {"再见", "拜拜", "bye", "谢谢", "感谢", "没了", "没有了"}
    return text.strip().lower() in farewells
