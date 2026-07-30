"""Bot 对话 Agent — 确定性路由实现

规则引擎做路由，LLM 做生成，asyncio 做并行。
不依赖任何 Agent 框架（LangGraph / PydanticAI）。

处理流程:
  classify_intent → 规则路由 {knowledge, business, fallback}
  → transfer_check → {respond, transfer}
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from lumio.services.bot.prompts import (
    _SUMMARIZE_SYSTEM_PROMPT,
    BUSINESS_SYSTEM_PROMPT,
    BUSINESS_TRANSFER_TEMPLATE,
    FALLBACK_SYSTEM_PROMPT,
    FAREWELL_RESPONSE,
    GREETING_RESPONSE,
    KNOWLEDGE_SYSTEM_PROMPT,
)
from lumio.services.bot.tool_executor import detect_confirmation
from lumio.services.bot.tool_selection import TOOL_INTENTS, select_tools_for_intent
from lumio.services.common.classifier import IntentClassifier, get_domain
from lumio.services.common.degradation import DegradationManager
from lumio.services.common.transfer import TransferChecker
from lumio.shared.config import get_settings
from lumio.shared.metrics import TOOL_CONFIRMATIONS
from lumio.shared.models import (
    DegradationLevel,
    Entity,
    IntentLabel,
    IntentResult,
    RetrieveRequest,
    RetrieveResponse,
    SentimentLabel,
)
from lumio.shared.tracing import traced

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch
    from pymilvus import Collection

    from lumio.services.bot.tool_executor import ToolCallingExecutor
    from lumio.services.common.embedding import EmbeddingCircuitBreaker
    from lumio.services.common.session import SessionManager
    from lumio.shared.models import PendingAction

logger = logging.getLogger(__name__)


# ── Token 估算（字符类感知，替代 len*2+20） ──


def _estimate_tokens(text: str) -> int:
    """基于字符类的 token 数估算

    CJK 字符: ~2 chars/token → 系数 0.55
    拉丁字母: ~4 chars/token → 系数 0.3
    其他(数字/标点/空格): ~1.2 chars/token → 系数 0.8
    +4 为消息格式开销（role/content 包装）
    """
    import re

    cjk = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", text))
    latin = len(re.findall(r"[a-zA-Z]", text))
    other = len(text) - cjk - latin
    return int(cjk * 0.55 + latin * 0.3 + other * 0.8) + 4


# ── 重要性标记 ──

_IMPORTANT_KEYWORDS = [
    "投诉", "举报", "银保监", "银监会", "人行", "央行",
    "律师函", "法务", "法院", "起诉",
    "盗刷", "挂失", "冻结", "风险",
    "承诺", "保证", "一定解决",
    "转人工", "人工客服",
]


def _is_important(content: str) -> bool:
    return any(kw in content for kw in _IMPORTANT_KEYWORDS)


class SmartCSAgent:
    """SmartCS 对话 Agent — 确定性路由

    规则引擎决定处理路径，LLM 仅用于内容生成。
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
        tool_executor: ToolCallingExecutor | None = None,
    ) -> None:
        self._classifier = classifier
        self._degradation_mgr = degradation_mgr
        self._transfer_checker = transfer_checker
        self._session_manager = session_manager
        self._es_client = es_client
        self._milvus_collection = milvus_collection
        self._embedding_breaker = embedding_breaker
        # 工具执行器（MCP_ENABLED=False 时为 None，走原有降级链，零回归）
        self._tool_executor = tool_executor

    # ── 公共接口 ──

    @traced("Agent: bot_run")
    async def run(self, session_id: str, user_input: str, customer_id: str | None = None) -> dict[str, Any]:
        """运行 Bot Agent，返回与旧版兼容的 dict"""
        # ── 跨会话画像学习（异步，首次对话时从历史推断客户画像）──
        if customer_id and self._session_manager:
            try:
                from lumio.services.bot.customer_memory import apply_learned_profile

                db_sf = getattr(self, "_db_session_factory", None)
                if db_sf:
                    asyncio.create_task(
                        apply_learned_profile(customer_id, session_id, db_sf, self._session_manager)
                    )
            except Exception:
                pass

        # ── 工具确认状态机拦截：存在未过期 pending_action 时，本轮解读为确认/取消 ──
        if self._tool_executor is not None and self._session_manager is not None:
            try:
                state = await self._session_manager.get_session(session_id)
            except Exception:
                state = None
            if state is not None and state.pending_action is not None:
                return await self._handle_pending_action(session_id, user_input, state, customer_id)

        # 快速路径：问候/告别不调 LLM
        if _is_greeting(user_input):
            return self._build_result(session_id, user_input, GREETING_RESPONSE, "template", "chitchat")
        if _is_farewell(user_input):
            return self._build_result(session_id, user_input, FAREWELL_RESPONSE, "template", "chitchat")

        try:
            # 1. 意图分类 + 实体抽取 + 情感分析
            intent_result, entities, sentiment = await self._classify(user_input)

            # 2. 规则路由
            domain = get_domain(intent_result.primary_intent)
            history = await self._load_history(session_id)

            # 2.5 渐进式工具暴露：仅当开关开启 + 有可用工具 + 命中查询类工具意图时，
            #     打通 MCP 工具编排路径（在 domain 分派之前）。开关关闭时整段不进入，路由 100% 同现状。
            if (
                get_settings().mcp.progressive_disclosure_enabled
                and self._tool_executor is not None
                and self._tool_executor.has_tools()
                and intent_result.primary_intent in TOOL_INTENTS
            ):
                return await self._handle_tool(
                    session_id, user_input, intent_result, history, entities, sentiment, customer_id
                )

            if domain == "knowledge":
                return await self._handle_knowledge(session_id, user_input, intent_result, history, entities, sentiment)
            elif domain == "business":
                return await self._handle_business(
                    session_id, user_input, intent_result, history, entities, sentiment, customer_id
                )
            else:
                return await self._handle_fallback(session_id, user_input, intent_result, history, entities, sentiment)

        except Exception as e:
            logger.warning("Bot Agent 执行失败: %s", e)
            return self._build_result(
                session_id,
                user_input,
                self._degradation_mgr._degrader.hardcoded_fallback(),
                "fallback",
                "faq",
            )

    # ── 路径处理 ──

    async def _classify(self, user_input: str) -> tuple[IntentResult, list[Entity], SentimentLabel]:
        """意图分类 + 实体抽取 + 情感分析"""
        try:
            intent_result, entities, sentiment, _ = await self._classifier.classify(user_input)
            return intent_result, entities, sentiment
        except Exception:
            return IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.0), [], SentimentLabel.NEUTRAL

    async def _handle_knowledge(
        self,
        session_id: str,
        user_input: str,
        intent: IntentResult,
        history: list[dict[str, str]],
        entities: list[Entity] | None = None,
        sentiment: SentimentLabel = SentimentLabel.NEUTRAL,
    ) -> dict[str, Any]:
        """知识问答: RAG 检索 + LLM 生成"""
        context = await self._retrieve(user_input)
        # 知识图谱实体关系增强：查询命中实体时，在 RAG 上下文后追加 KG 关系补充
        if context:
            from lumio.services.bot.knowledge_graph import enrich_retrieval_context

            context = enrich_retrieval_context(user_input, [context])

        # 槽位追踪：加载/创建 + 实体填充 + 注入 prompt
        slot_prompt = await self._load_slot_prompt(session_id, intent.primary_intent, entities or [])

        # 结构化会话记忆注入 system prompt（永不裁剪）
        session_memory = await self._build_session_memory(session_id)
        system_prompt = KNOWLEDGE_SYSTEM_PROMPT
        if session_memory:
            system_prompt = f"{KNOWLEDGE_SYSTEM_PROMPT}\n\n## 会话记忆\n{session_memory}"
        if slot_prompt:
            system_prompt = f"{system_prompt}\n\n{slot_prompt}"
        result = await self._degradation_mgr.generate_with_fallback(
            system_prompt=system_prompt,
            user_input=user_input,
            context=context,
            intent_label=intent.primary_intent,
            history=history,
        )
        # 转人工检查
        should_transfer, transfer_reason = await self._check_transfer(user_input, intent, sentiment)
        return self._build_result(
            session_id,
            user_input,
            result.content,
            result.source,
            intent.primary_intent.value,
            intent.primary_confidence,
            should_transfer,
            transfer_reason,
            entities,
            sentiment,
        )

    async def _handle_business(
        self,
        session_id: str,
        user_input: str,
        intent: IntentResult,
        history: list[dict[str, str]],
        entities: list[Entity] | None = None,
        sentiment: SentimentLabel = SentimentLabel.NEUTRAL,
        customer_id: str | None = None,
    ) -> dict[str, Any]:
        """业务办理: 挂失/投诉直接转，否则（有工具）走工具编排 / （无工具）LLM 生成"""
        if intent.primary_intent in (IntentLabel.CARD_LOSS, IntentLabel.COMPLAINT, IntentLabel.TRANSFER_AGENT):
            reason_map = {
                IntentLabel.CARD_LOSS: "挂失业务",
                IntentLabel.COMPLAINT: "投诉处理",
                IntentLabel.TRANSFER_AGENT: "客户主动请求",
            }
            reason = reason_map.get(intent.primary_intent, "业务办理")
            return self._build_result(
                session_id,
                user_input,
                BUSINESS_TRANSFER_TEMPLATE.format(reason=reason),
                "template",
                intent.primary_intent.value,
                intent.primary_confidence,
                should_transfer=True,
                transfer_reason=reason,
            )

        # 结构化会话记忆注入 system prompt
        session_memory = await self._build_session_memory(session_id)
        system_prompt = BUSINESS_SYSTEM_PROMPT
        if session_memory:
            system_prompt = f"{BUSINESS_SYSTEM_PROMPT}\n\n## 会话记忆\n{session_memory}"

        # 工具编排路径：MCP 启用且有可用工具时，尝试 tool-calling；任何异常回落降级链
        if self._tool_executor is not None and self._tool_executor.has_tools():
            try:
                tool_result = await self._tool_executor.run_conversation(
                    system_prompt=system_prompt,
                    user_input=user_input,
                    history=history,
                    session_id=session_id,
                    actor_id=customer_id or session_id,
                    actor_role="customer",
                )
                if tool_result.pending_action is not None:
                    # 敏感操作：暂存待确认，返回确认话术，不执行
                    await self._save_pending_action(session_id, tool_result.pending_action)
                    return self._build_result(
                        session_id,
                        user_input,
                        tool_result.content,
                        "tool_confirm",
                        intent.primary_intent.value,
                        intent.primary_confidence,
                    )
                return self._build_result(
                    session_id,
                    user_input,
                    tool_result.content,
                    tool_result.source,
                    intent.primary_intent.value,
                    intent.primary_confidence,
                    entities=entities,
                    sentiment=sentiment,
                )
            except Exception as exc:
                logger.warning("工具编排失败，回落降级链: %s", exc)

        result = await self._degradation_mgr.generate_with_fallback(
            system_prompt=system_prompt,
            user_input=user_input,
            context="",
            intent_label=intent.primary_intent,
            history=history,
        )
        should_transfer, transfer_reason = await self._check_transfer(user_input, intent, sentiment)
        return self._build_result(
            session_id,
            user_input,
            result.content,
            result.source,
            intent.primary_intent.value,
            intent.primary_confidence,
            should_transfer,
            transfer_reason,
            entities,
            sentiment,
        )

    async def _handle_tool(
        self,
        session_id: str,
        user_input: str,
        intent: IntentResult,
        history: list[dict[str, str]],
        entities: list[Entity] | None = None,
        sentiment: SentimentLabel = SentimentLabel.NEUTRAL,
        customer_id: str | None = None,
    ) -> dict[str, Any]:
        """工具编排路径（渐进式暴露）：查询类意图打通 MCP 工具

        - 依据意图/置信度选择工具子集（``None`` = 暴露全量）。
        - 敏感写操作返回 ``pending_action``，暂存待确认（复用确认状态机）。
        - 任何异常回落知识问答（RAG），保证优雅降级。
        """
        tool_names = select_tools_for_intent(intent.primary_intent, intent.primary_confidence, get_settings().mcp)

        session_memory = await self._build_session_memory(session_id)
        system_prompt = BUSINESS_SYSTEM_PROMPT
        if session_memory:
            system_prompt = f"{BUSINESS_SYSTEM_PROMPT}\n\n## 会话记忆\n{session_memory}"

        try:
            tool_result = await self._tool_executor.run_conversation(  # type: ignore[union-attr]
                system_prompt=system_prompt,
                user_input=user_input,
                history=history,
                session_id=session_id,
                actor_id=customer_id or session_id,
                actor_role="customer",
                tool_names=tool_names,
            )
        except Exception as exc:
            logger.warning("工具编排失败，回落知识问答: %s", exc)
            return await self._handle_knowledge(session_id, user_input, intent, history, entities, sentiment)

        if tool_result.pending_action is not None:
            # 敏感操作：暂存待确认，返回确认话术，不执行
            await self._save_pending_action(session_id, tool_result.pending_action)
            return self._build_result(
                session_id,
                user_input,
                tool_result.content,
                "tool_confirm",
                intent.primary_intent.value,
                intent.primary_confidence,
            )
        return self._build_result(
            session_id,
            user_input,
            tool_result.content,
            tool_result.source,
            intent.primary_intent.value,
            intent.primary_confidence,
            entities=entities,
            sentiment=sentiment,
        )

    # ── 工具确认状态机 ──

    async def _handle_pending_action(
        self,
        session_id: str,
        user_input: str,
        state: Any,
        customer_id: str | None,
    ) -> dict[str, Any]:
        """处理待确认的敏感工具操作（confirm/cancel/unclear/expired）"""
        pending: PendingAction = state.pending_action
        actor_id = customer_id or state.customer_id or session_id

        # 过期：清除并提示重新发起
        if pending.expires_at is not None and datetime.now(UTC) > pending.expires_at:
            await self._clear_pending_action(session_id, state.version)
            TOOL_CONFIRMATIONS.labels(decision="expired").inc()
            await self._tool_executor.audit_decision(  # type: ignore[union-attr]
                session_id=session_id, actor_id=actor_id, actor_role="customer",
                tool_name=pending.tool_name, decision="expired",
            )
            return self._build_result(
                session_id, user_input, "您上一步的操作请求已超时失效，如仍需办理请重新告知我。", "template", "faq"
            )

        decision = detect_confirmation(user_input)

        if decision == "confirm":
            await self._tool_executor.audit_decision(  # type: ignore[union-attr]
                session_id=session_id, actor_id=actor_id, actor_role="customer",
                tool_name=pending.tool_name, decision="confirm",
            )
            try:
                session_memory = await self._build_session_memory(session_id)
                system_prompt = BUSINESS_SYSTEM_PROMPT
                if session_memory:
                    system_prompt = f"{BUSINESS_SYSTEM_PROMPT}\n\n## 会话记忆\n{session_memory}"
                history = await self._load_history(session_id)
                tool_result = await self._tool_executor.execute_confirmed_action(  # type: ignore[union-attr]
                    pending=pending,
                    system_prompt=system_prompt,
                    history=history,
                    session_id=session_id,
                    actor_id=actor_id,
                    actor_role="customer",
                )
                await self._clear_pending_action(session_id, state.version)
                TOOL_CONFIRMATIONS.labels(decision="confirm").inc()
                return self._build_result(session_id, user_input, tool_result.content, tool_result.source, "faq")
            except Exception as exc:
                logger.warning("确认执行工具失败，清除待确认并降级: %s", exc)
                await self._clear_pending_action(session_id, state.version)
                return self._build_result(
                    session_id, user_input, self._degradation_mgr._degrader.hardcoded_fallback(), "fallback", "faq"
                )

        if decision == "cancel":
            await self._clear_pending_action(session_id, state.version)
            TOOL_CONFIRMATIONS.labels(decision="cancel").inc()
            await self._tool_executor.audit_decision(  # type: ignore[union-attr]
                session_id=session_id, actor_id=actor_id, actor_role="customer",
                tool_name=pending.tool_name, decision="cancel",
            )
            return self._build_result(
                session_id, user_input, "好的，已为您取消该操作。还有什么可以帮您的吗？", "template", "faq"
            )

        # unclear：重复确认话术，不清除
        TOOL_CONFIRMATIONS.labels(decision="unclear").inc()
        return self._build_result(session_id, user_input, pending.confirm_prompt, "template", "faq")

    async def _save_pending_action(self, session_id: str, pending: PendingAction) -> None:
        """将待确认操作写入会话状态（CAS）"""
        if self._session_manager is None:
            return
        try:
            state = await self._session_manager.get_session(session_id)
            if state is None:
                return
            await self._session_manager.patch_state(
                session_id=session_id,
                expected_version=state.version,
                patches={"pending_action": pending.model_dump(mode="json")},
                writer="bot_agent:tool_confirm",
            )
        except Exception:
            logger.debug("写入 pending_action 失败: session=%s", session_id)

    async def _clear_pending_action(self, session_id: str, expected_version: int) -> None:
        """清除待确认操作"""
        if self._session_manager is None:
            return
        try:
            await self._session_manager.patch_state(
                session_id=session_id,
                expected_version=expected_version,
                patches={"pending_action": None},
                writer="bot_agent:tool_confirm",
            )
        except Exception:
            logger.debug("清除 pending_action 失败: session=%s", session_id)

    async def _handle_fallback(
        self,
        session_id: str,
        user_input: str,
        intent: IntentResult,
        history: list[dict[str, str]],
        entities: list[Entity] | None = None,
        sentiment: SentimentLabel = SentimentLabel.NEUTRAL,
    ) -> dict[str, Any]:
        """闲聊/兜底: 快速匹配 或 LLM 生成"""
        # 结构化会话记忆注入 system prompt
        session_memory = await self._build_session_memory(session_id)
        system_prompt = FALLBACK_SYSTEM_PROMPT
        if session_memory:
            system_prompt = f"{FALLBACK_SYSTEM_PROMPT}\n\n## 会话记忆\n{session_memory}"
        result = await self._degradation_mgr.generate_with_fallback(
            system_prompt=system_prompt,
            user_input=user_input,
            context="",
            intent_label=IntentLabel.CHITCHAT,
            history=history,
        )
        return self._build_result(
            session_id,
            user_input,
            result.content,
            result.source,
            "chitchat",
            0.0,
            entities=entities,
            sentiment=sentiment,
        )

    # ── 辅助方法 ──

    async def _retrieve(self, query: str) -> str:
        """RAG 检索"""
        if self._degradation_mgr.level == DegradationLevel.FALLBACK:
            return ""
        try:
            from lumio.services.common.retrieval import retrieve as do_retrieve

            settings = get_settings()
            embedding_provider = (
                self._embedding_breaker.provider
                if self._embedding_breaker and self._embedding_breaker.is_available
                else None
            )
            resp: RetrieveResponse = await do_retrieve(
                request=RetrieveRequest(query=query, top_k=settings.rag.top_k, rerank=False),
                es_client=self._es_client,
                milvus_collection=self._milvus_collection,
                embedding_provider=embedding_provider,
                reranker=None,
            )
            if resp.results:
                return "\n\n".join(f"[{i + 1}] {r.content}" for i, r in enumerate(resp.results))
        except Exception as e:
            logger.warning("知识检索失败: %s", e)
        return ""

    async def _check_transfer(
        self,
        text: str,
        intent: IntentResult | None = None,
        sentiment: SentimentLabel = SentimentLabel.NEUTRAL,
    ) -> tuple[bool, str]:
        """判断是否需要转人工"""
        if self._transfer_checker is None:
            return False, ""
        try:
            should, _, reason = self._transfer_checker.check(
                text=text,
                intent=intent or IntentResult(primary_intent=IntentLabel.FAQ),
                sentiment=sentiment,
                session=None,
            )
            return should, reason
        except Exception:
            return False, ""

    async def _load_history(self, session_id: str) -> list[dict[str, str]]:
        """加载对话历史作为 LLM 上下文

        三层上下文策略（银行客服最佳实践）:
        - Layer 1: 结构化会话记忆 + 对话摘要（注入 system prompt，永不裁剪）
        - Layer 2: 近期对话历史（token 预算裁剪，被裁剪部分生成摘要）
        - Layer 3: 检索知识（RAG context，由调用方传入）

        摘要触发条件: 当 token 预算导致轮次被裁剪时，对被裁剪的轮次生成摘要，
        保存到 SessionState.conversation_summary。后续轮次复用已有摘要，
        只对新增的裁剪轮次增量摘要（避免每轮都调 LLM）。
        """
        try:
            turns = await self._session_manager.get_history(session_id, limit=20)
            if not turns:
                return []

            settings = get_settings()
            max_tokens = getattr(settings.llm, "max_context_tokens", 4096)
            reserved = getattr(settings.llm, "reserved_tokens", 2048)
            budget = max(max_tokens - reserved, 1024)

            # 从最近向前累加，找出 token 预算内的轮次
            # 关键轮次（投诉/承诺/转人工）不会被裁剪
            kept_turns: list = []
            used = 0
            split_idx = len(turns)
            for i in range(len(turns) - 1, -1, -1):
                t = turns[i]
                est = _estimate_tokens(t.content)
                is_important = _is_important(t.content)
                if used + est > budget and kept_turns and not is_important:
                    split_idx = i + 1
                    break
                kept_turns.insert(0, t)
                used += est

            # 如果有轮次被裁剪，异步触发摘要压缩（不阻塞用户请求）
            trimmed_turns = turns[:split_idx]
            if trimmed_turns:
                asyncio.create_task(self._ensure_summary(session_id, trimmed_turns))

            return [
                {"role": "user" if t.speaker == "customer" else "assistant", "content": t.content} for t in kept_turns
            ]
        except Exception:
            return []

    async def _ensure_summary(self, session_id: str, trimmed_turns: list) -> None:
        """确保被裁剪的轮次已生成摘要

        用 last_summarized_turn_id 精确追踪已摘要位置，避免 LTRIM 导致计数失准。

        增量策略:
        - 在 trimmed_turns 中查找 last_summarized_turn_id 的位置
        - 如果找到：对该位置之后的轮次生成增量摘要
        - 如果未找到（LTRIM 删除了已摘要的轮次）：对所有 trimmed_turns 重新生成摘要
        - LLM 不可用时跳过（降级为无摘要，结构化记忆仍保证关键实体不丢）
        """
        try:
            state = await self._session_manager.get_session(session_id)
            if state is None:
                return

            last_summarized_id = state.last_summarized_turn_id
            last_turn = trimmed_turns[-1]

            # 最后一个裁剪轮次已被摘要，无需更新
            if last_turn.turn_id == last_summarized_id:
                return

            # 在 trimmed_turns 中查找已摘要位置
            split_idx = 0
            if last_summarized_id:
                for i, t in enumerate(trimmed_turns):
                    if t.turn_id == last_summarized_id:
                        split_idx = i + 1
                        break
                # 如果未找到（LTRIM 删除了已摘要轮次），split_idx 保持 0，重新摘要全部

            new_turns = trimmed_turns[split_idx:]
            if not new_turns:
                return

            # 构造摘要 prompt
            conversation = "\n".join(
                f"[{ {'customer': '客户', 'agent': '坐席', 'bot': '机器人'}.get(t.speaker, t.speaker) }] {t.content}"
                for t in new_turns
            )

            existing_summary = state.conversation_summary if split_idx > 0 else ""

            summary_prompt = _SUMMARIZE_SYSTEM_PROMPT
            user_content = (
                f"已有摘要：\n{existing_summary}\n\n新增对话：\n{conversation}"
                if existing_summary
                else f"对话记录：\n{conversation}"
            )

            # 获取 LLM client
            llm_client = self._degradation_mgr._llm
            if llm_client is None:
                return

            try:
                new_summary = await llm_client.chat(
                    messages=[
                        {"role": "system", "content": summary_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    timeout=3.0,
                )
            except Exception:
                logger.debug("对话摘要生成失败: session=%s", session_id)
                return

            if not new_summary or not new_summary.strip():
                return

            # 用 patch_state 增量写入摘要
            result = await self._session_manager.patch_state(
                session_id=session_id,
                expected_version=state.version,
                patches={
                    "conversation_summary": new_summary.strip(),
                    "summary_turn_count": len(trimmed_turns),
                    "last_summarized_turn_id": last_turn.turn_id,
                },
                writer="bot_agent:summary",
            )
            if result.get("ok"):
                logger.info(
                    "对话摘要已更新: session=%s trimmed=%d new=%d",
                    session_id,
                    len(trimmed_turns),
                    len(new_turns),
                )
            else:
                logger.warning("对话摘要 CAS 写入失败: session=%s", session_id)
        except Exception:
            logger.debug("摘要更新异常: session=%s", session_id)

    async def _build_session_memory(self, session_id: str) -> str:
        """构建结构化会话记忆（注入 system prompt，永不裁剪）

        银行客服核心需求：即使对话历史被裁剪，关键实体（卡号、金额、日期）
        和意图栈仍需保留，避免 Bot 重复收集敏感信息。

        记忆内容:
        - 对话摘要: 被裁剪轮次的摘要（对话脉络、Bot 承诺、处理进度）
        - 客户画像: VIP等级、卡种、风险偏好
        - 实体池: 对话中已抽取的实体（卡号、金额、日期等）
        - 意图栈: 客户的意图历史
        """
        try:
            state = await self._session_manager.get_session(session_id)
            if state is None:
                return ""

            parts: list[str] = []

            # 对话摘要（被裁剪轮次的脉络，最高优先级）
            if state.conversation_summary:
                parts.append(f"[对话摘要]\n{state.conversation_summary}")

            # 客户画像
            profile_parts: list[str] = []
            if state.vip_level and state.vip_level != "普通":
                profile_parts.append(f"VIP等级={state.vip_level}")
            if state.card_types:
                profile_parts.append(f"卡种={','.join(state.card_types)}")
            if state.risk_tolerance and state.risk_tolerance != "R2":
                profile_parts.append(f"风险偏好={state.risk_tolerance}")
            if profile_parts:
                parts.append(f"[客户画像] {', '.join(profile_parts)}")

            # 实体池（从 last_entities 读取，已由 add_turn 维护）
            if state.last_entities:
                entity_strs = [f"{e.entity_type}={e.value}" for e in state.last_entities if e.entity_type and e.value]
                if entity_strs:
                    parts.append(f"[已知实体] {', '.join(entity_strs)}")

            # 意图栈
            if state.intent_stack:
                intent_strs = [i.value if hasattr(i, "value") else str(i) for i in state.intent_stack]
                parts.append(f"[意图历史] {' → '.join(intent_strs)}")

            # 最近意图
            if state.last_intent:
                parts.append(f"[当前意图] {state.last_intent.value}")

            if not parts:
                return ""

            return "\n".join(parts)
        except Exception:
            logger.debug("构建会话记忆失败: session=%s", session_id)

    async def _load_slot_prompt(self, session_id: str, intent: IntentLabel, entities: list[Entity]) -> str:
        """加载/创建槽位追踪器，从实体池填充，返回槽位 prompt 段

        槽位状态持久化在 Redis key smartcs:slot:{session_id}，跨轮次保留。
        仅当意图有定义的必填槽位时才返回非空 prompt。
        """
        import json

        from lumio.services.bot.slot_tracker import SlotTracker

        redis = self._session_manager._redis if self._session_manager else None

        # 读取已有 tracker
        tracker: SlotTracker | None = None
        if redis:
            try:
                raw = await redis.get(f"smartcs:slot:{session_id}")
                if raw:
                    data = json.loads(raw)
                    # 意图切换时重置 tracker
                    if data.get("intent") == intent.value:
                        tracker = SlotTracker.from_dict(data)
            except Exception:
                pass

        # 创建新 tracker
        if tracker is None:
            tracker = SlotTracker.for_intent(intent)

        # 从实体池填充槽位
        if entities:
            entity_dicts = [{"entity_type": e.entity_type, "value": e.value} for e in entities if e.entity_type and e.value]
            tracker.fill_from_entities(entity_dicts)

        # 持久化
        if redis:
            try:
                await redis.setex(f"smartcs:slot:{session_id}", 3600, json.dumps(tracker.to_dict(), ensure_ascii=False))
            except Exception:
                pass

        return tracker.build_prompt() if tracker.has_slots else ""

    def _build_result(
        self,
        session_id: str,
        user_input: str,
        response: str,
        response_source: str,
        primary_intent: str = "faq",
        primary_confidence: float = 0.0,
        should_transfer: bool = False,
        transfer_reason: str = "",
        entities: list[Entity] | None = None,
        sentiment: SentimentLabel = SentimentLabel.NEUTRAL,
    ) -> dict[str, Any]:
        try:
            intent_label = IntentLabel(primary_intent)
        except ValueError:
            intent_label = IntentLabel.FAQ

        return {
            "session_id": session_id,
            "user_input": user_input,
            "intent": IntentResult(primary_intent=intent_label, primary_confidence=primary_confidence),
            "entities": entities or [],
            "sentiment": sentiment,
            "classify_source": "",
            "domain": get_domain(intent_label),
            "retrieval_context": "",
            "response": response,
            "response_source": response_source,
            "should_transfer": should_transfer,
            "transfer_reason": transfer_reason,
            "session_state": None,
        }


# ── 快速路径判断 ──


def _is_greeting(text: str) -> bool:
    return text.strip().lower() in {"你好", "您好", "嗨", "hi", "hello", "在吗", "在不在"}


def _is_farewell(text: str) -> bool:
    return text.strip().lower() in {"再见", "拜拜", "bye", "谢谢", "感谢", "没了", "没有了"}
