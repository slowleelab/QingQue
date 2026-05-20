"""会话状态管理

通过 LangGraph state 读写会话状态（单一状态源），
Redis 仅用于 LangGraph Checkpointer 持久化 + 会话元信息缓存。
拆分 meta/history 键避免长对话全量读写。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis

from smartcs.shared.config import get_settings
from smartcs.shared.models import (
    ChannelType,
    DialogueTurn,
    Entity,
    IntentLabel,
    IntentResult,
    SessionPhase,
    SessionState,
    SessionSubPhase,
    validate_transition,
)

logger = logging.getLogger(__name__)

# Redis Key 前缀
_META_PREFIX = "smartcs:session"
_HISTORY_PREFIX = "smartcs:session"


class SessionManager:
    """会话状态管理器

    职责：
    - 创建/加载/更新会话状态
    - 管理对话历史（append-only）
    - 转人工判断所需的置信度计数
    - 阶段生命周期管理

    设计原则：
    - 不与 LangGraph Checkpointer 竞争状态管理
    - meta 键存会话元信息（小，频繁更新）
    - history 键用 Redis List 存对话历史（append-only，高效）
    - 从 history 中提取意图/实体信息，不冗余存储
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        settings = get_settings()
        self._ttl = settings.session.ttl_seconds
        self._max_turns = settings.session.max_turns
        self._timeout_manager: Any = None  # SessionTimeoutManager, set via set_timeout_manager

    def set_timeout_manager(self, manager: Any) -> None:
        """设置超时管理器"""
        self._timeout_manager = manager

    def _meta_key(self, session_id: str) -> str:
        return f"{_META_PREFIX}:{session_id}:meta"

    def _history_key(self, session_id: str) -> str:
        return f"{_HISTORY_PREFIX}:{session_id}:history"

    async def create_session(
        self,
        *,
        customer_id: str | None = None,
        channel_type: ChannelType = ChannelType.WEB,
    ) -> SessionState:
        """创建新会话

        Args:
            customer_id: 客户 ID
            channel_type: 渠道类型

        Returns:
            新创建的 SessionState
        """
        session_id = uuid4().hex
        now = datetime.now()
        state = SessionState(
            session_id=session_id,
            customer_id=customer_id,
            channel_type=channel_type,
            current_phase=SessionPhase.BOT,
            sub_phase=SessionSubPhase.BOT_ACTIVE,
            created_at=now,
            last_active_at=now,
        )
        await self._save_meta(state)
        return state

    async def get_session(self, session_id: str) -> SessionState | None:
        """加载会话状态

        Args:
            session_id: 会话 ID

        Returns:
            SessionState 或 None（会话不存在）
        """
        meta_json = await self._redis.get(self._meta_key(session_id))
        if meta_json is None:
            return None

        meta = json.loads(meta_json)
        turns = await self._load_history(session_id)

        return SessionState(
            session_id=meta["session_id"],
            customer_id=meta.get("customer_id"),
            channel_type=ChannelType(meta.get("channel_type", "web")),
            current_phase=SessionPhase(meta.get("current_phase", "bot")),
            sub_phase=SessionSubPhase(meta["sub_phase"]) if meta.get("sub_phase") else None,
            end_reason=meta.get("end_reason"),
            vip_level=meta.get("vip_level", "普通"),
            card_types=meta.get("card_types", []),
            risk_tolerance=meta.get("risk_tolerance", "R2"),
            turns=turns,
            turn_count=len(turns),
            last_intent=IntentLabel(meta["last_intent"]) if meta.get("last_intent") else None,
            last_entities=[Entity(**e) for e in meta.get("last_entities", [])],
            confidence_history=meta.get("confidence_history", []),
            low_confidence_streak=meta.get("low_confidence_streak", 0),
            human_request_score=meta.get("human_request_score", 0),
            agent_id=meta.get("agent_id"),
            transfer_reason=meta.get("transfer_reason"),
            transfer_summary=meta.get("transfer_summary"),
            created_at=datetime.fromisoformat(meta["created_at"]) if meta.get("created_at") else datetime.now(),
            last_active_at=datetime.fromisoformat(meta["last_active_at"]) if meta.get("last_active_at") else datetime.now(),
            version=meta.get("version", 1),
        )

    async def add_turn(
        self,
        session_id: str,
        turn: DialogueTurn,
        *,
        intent: IntentResult | None = None,
    ) -> SessionState:
        """追加对话轮次

        自动更新低置信度计数和最后意图。

        Args:
            session_id: 会话 ID
            turn: 对话轮次
            intent: 本轮意图分类结果

        Returns:
            更新后的 SessionState
        """
        state = await self.get_session(session_id)
        if state is None:
            raise ValueError(f"会话不存在: {session_id}")

        # 追加对话历史到 Redis List
        turn_json = turn.model_dump_json()
        await self._redis.rpush(self._history_key(session_id), turn_json)

        # 保持历史窗口
        history_len = await self._redis.llen(self._history_key(session_id))
        if history_len > self._max_turns:
            await self._redis.ltrim(self._history_key(session_id), -self._max_turns, -1)

        # 更新 meta
        state.turns.append(turn)
        state.turn_count = min(history_len + 1, self._max_turns)
        state.last_active_at = datetime.now()

        if intent:
            state.last_intent = intent.primary_intent
            state.confidence_history.append(intent.primary_confidence)
            # 保留最近 20 个置信度记录
            if len(state.confidence_history) > 20:
                state.confidence_history = state.confidence_history[-20:]

            # 更新低置信度计数
            settings = get_settings()
            if intent.primary_confidence < settings.classification.intent_threshold:
                state.low_confidence_streak += 1
            else:
                state.low_confidence_streak = 0

        await self._save_meta(state)
        return state

    async def get_history(self, session_id: str, limit: int | None = None) -> list[DialogueTurn]:
        """获取最近的对话历史

        Args:
            session_id: 会话 ID
            limit: 返回最近 N 轮，None 时返回全部

        Returns:
            DialogueTurn 列表（按时间升序）
        """
        return await self._load_history(session_id, limit=limit)

    async def transition_phase(
        self,
        session_id: str,
        new_phase: SessionPhase,
        *,
        new_sub_phase: SessionSubPhase | None = None,
        reason: str = "",
    ) -> SessionState:
        """切换会话阶段

        Args:
            session_id: 会话 ID
            new_phase: 新阶段
            new_sub_phase: 新子阶段，None 时根据 new_phase 自动推断
            reason: 阶段切换原因

        Returns:
            更新后的 SessionState

        Raises:
            ValueError: 会话不存在或转换不合法
        """
        state = await self.get_session(session_id)
        if state is None:
            raise ValueError(f"会话不存在: {session_id}")

        # 自动推断子阶段
        if new_sub_phase is None:
            if new_phase == SessionPhase.BOT:
                new_sub_phase = SessionSubPhase.BOT_ACTIVE
            elif new_phase == SessionPhase.ENDED:
                new_sub_phase = None
            elif new_phase == SessionPhase.AGENT:
                new_sub_phase = SessionSubPhase.AG_QUEUED

        # 校验转换合法性
        if state.sub_phase is not None and new_sub_phase is not None:
            if not validate_transition(state.current_phase, state.sub_phase, new_sub_phase):
                raise ValueError(
                    f"非法状态转换: session={session_id} "
                    f"{state.sub_phase.value} → {new_sub_phase.value}"
                )

        old_phase = state.current_phase
        old_sub = state.sub_phase
        state.current_phase = new_phase
        state.sub_phase = new_sub_phase
        state.last_active_at = datetime.now()

        if new_phase == SessionPhase.AGENT and reason:
            state.transfer_reason = reason

        if new_phase == SessionPhase.ENDED:
            state.end_reason = reason or state.end_reason

        await self._save_meta(state)
        logger.info(
            "会话 %s 阶段切换: %s:%s → %s:%s (原因: %s)",
            session_id,
            old_phase.value, old_sub.value if old_sub else "-",
            new_phase.value, new_sub_phase.value if new_sub_phase else "-",
            reason,
        )

        # 启动/取消超时守卫
        if self._timeout_manager:
            if new_phase == SessionPhase.ENDED:
                self._timeout_manager.cancel_guard(session_id)
            elif new_sub_phase:
                self._timeout_manager.start_guard(session_id, new_sub_phase)

        return state

    async def get_or_create(
        self,
        session_id: str | None,
        *,
        customer_id: str | None = None,
        channel_type: ChannelType = ChannelType.WEB,
    ) -> SessionState:
        """获取或创建会话

        Args:
            session_id: 会话 ID（None 时创建新会话）
            customer_id: 客户 ID
            channel_type: 渠道类型

        Returns:
            SessionState
        """
        if session_id:
            state = await self.get_session(session_id)
            if state:
                return state
        return await self.create_session(customer_id=customer_id, channel_type=channel_type)

    async def delete_session(self, session_id: str) -> None:
        """删除会话"""
        await self._redis.delete(self._meta_key(session_id), self._history_key(session_id))

    # ── 内部方法 ──

    async def _save_meta(self, state: SessionState) -> None:
        """保存会话元信息到 Redis"""
        meta: dict[str, Any] = {
            "session_id": state.session_id,
            "customer_id": state.customer_id,
            "channel_type": state.channel_type.value,
            "current_phase": state.current_phase.value,
            "sub_phase": state.sub_phase.value if state.sub_phase else None,
            "end_reason": state.end_reason,
            "vip_level": state.vip_level,
            "card_types": state.card_types,
            "risk_tolerance": state.risk_tolerance,
            "turn_count": state.turn_count,
            "last_intent": state.last_intent.value if state.last_intent else None,
            "last_entities": [e.model_dump() for e in state.last_entities],
            "confidence_history": state.confidence_history,
            "low_confidence_streak": state.low_confidence_streak,
            "human_request_score": state.human_request_score,
            "agent_id": state.agent_id,
            "transfer_reason": state.transfer_reason,
            "transfer_summary": state.transfer_summary,
            "created_at": state.created_at.isoformat(),
            "last_active_at": state.last_active_at.isoformat(),
            "version": state.version,
        }
        await self._redis.set(
            self._meta_key(state.session_id),
            json.dumps(meta, ensure_ascii=False),
            ex=self._ttl,
        )

    async def _load_history(self, session_id: str, limit: int | None = None) -> list[DialogueTurn]:
        """从 Redis List 加载对话历史"""
        key = self._history_key(session_id)
        if limit:
            raw_list = await self._redis.lrange(key, -limit, -1)
        else:
            raw_list = await self._redis.lrange(key, 0, -1)

        turns: list[DialogueTurn] = []
        for raw in raw_list:
            try:
                turns.append(DialogueTurn.model_validate_json(raw))
            except Exception:
                logger.warning("对话轮次解析失败: session_id=%s", session_id)
        return turns
