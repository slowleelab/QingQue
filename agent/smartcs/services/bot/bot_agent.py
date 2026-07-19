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
from typing import TYPE_CHECKING, Any

from smartcs.services.bot.prompts import (
    _SUMMARIZE_SYSTEM_PROMPT,
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
from smartcs.shared.config import get_settings
from smartcs.shared.models import (
    DegradationLevel,
    Entity,
    IntentLabel,
    IntentResult,
    RetrieveRequest,
    RetrieveResponse,
    SentimentLabel,
)
from smartcs.shared.tracing import traced

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch
    from pymilvus import Collection

    from smartcs.services.common.embedding import EmbeddingCircuitBreaker
    from smartcs.services.common.session import SessionManager

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._classifier = classifier
        self._degradation_mgr = degradation_mgr
        self._transfer_checker = transfer_checker
        self._session_manager = session_manager
        self._es_client = es_client
        self._milvus_collection = milvus_collection
        self._embedding_breaker = embedding_breaker

    # ── 公共接口 ──

    @traced("Agent: bot_run")
    async def run(self, session_id: str, user_input: str) -> dict[str, Any]:
        """运行 Bot Agent，返回与旧版兼容的 dict"""
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

            if domain == "knowledge":
                return await self._handle_knowledge(session_id, user_input, intent_result, history, entities, sentiment)
            elif domain == "business":
                return await self._handle_business(session_id, user_input, intent_result, history, entities, sentiment)
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
        # 结构化会话记忆注入 system prompt（永不裁剪）
        session_memory = await self._build_session_memory(session_id)
        system_prompt = KNOWLEDGE_SYSTEM_PROMPT
        if session_memory:
            system_prompt = f"{KNOWLEDGE_SYSTEM_PROMPT}\n\n## 会话记忆\n{session_memory}"
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
    ) -> dict[str, Any]:
        """业务办理: 挂失/投诉直接转，否则 LLM 生成"""
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
            from smartcs.services.common.retrieval import retrieve as do_retrieve

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
            kept_turns: list = []
            used = 0
            split_idx = len(turns)  # 被裁剪轮次的分界点
            for i in range(len(turns) - 1, -1, -1):
                t = turns[i]
                est = len(t.content) * 2 + 20
                if used + est > budget and kept_turns:
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
            return ""

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
