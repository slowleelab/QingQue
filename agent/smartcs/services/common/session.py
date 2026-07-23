"""会话状态管理

通过 LangGraph state 读写会话状态（单一状态源），
Redis 仅用于 LangGraph Checkpointer 持久化 + 会话元信息缓存。
拆分 meta/history 键避免长对话全量读写。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis

from smartcs.shared.config import get_settings
from smartcs.shared.exceptions import InvalidTransitionError, SessionNotFoundError
from smartcs.shared.metrics import SESSION_PHASE_DURATION, SESSION_TRANSITIONS
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

# ── CAS Lua 脚本（统一状态层，替代 StateManager 的独立 key）──

_CAS_WRITE_SCRIPT = """
local key = KEYS[1]
local expected_version = tonumber(ARGV[1])
local patch_json = ARGV[2]
local writer = ARGV[3]
local ttl = tonumber(ARGV[4])

local raw = redis.call('GET', key)
if raw == false then
    return cjson.encode({ok = false, current_version = 0, reason = 'not_found'})
end

local current = cjson.decode(raw)
if current.version ~= expected_version then
    return cjson.encode({ok = false, current_version = current.version, reason = 'version_mismatch'})
end

current.version = current.version + 1
current.last_writer = writer
current.updated_at = ARGV[5]

local patches = cjson.decode(patch_json)
for k, v in pairs(patches) do
    current[k] = v
end

redis.call('SET', key, cjson.encode(current), 'EX', ttl)
return cjson.encode({ok = true, new_version = current.version})
"""

# 增量合并字段（Python 侧处理，Lua 侧不感知）
_INCREMENTAL_FIELDS = {"intent_stack", "entity_pool"}
_ONE_WAY_GATE_FIELDS = {"suppress_flag"}


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
        self._script_sha: str | None = None  # CAS Lua 脚本 SHA 缓存
        self._db_session_factory: Any = None  # SQLAlchemy async session factory, set via set_db_session_factory
        self._pending_persist_tasks: set[asyncio.Task] = set()  # 跟踪持久化任务，防止进程关闭时丢失

    def set_timeout_manager(self, manager: Any) -> None:
        """设置超时管理器"""
        self._timeout_manager = manager

    def set_db_session_factory(self, factory: Any) -> None:
        """设置数据库会话工厂（用于对话记录持久化）"""
        self._db_session_factory = factory

    async def flush_pending_persists(self, timeout: float = 10.0) -> None:
        """等待所有待完成的持久化任务完成（进程关闭时调用）"""
        if not self._pending_persist_tasks:
            return
        logger.info("等待 %d 个持久化任务完成...", len(self._pending_persist_tasks))
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._pending_persist_tasks, return_exceptions=True),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("持久化任务等待超时，部分对话记录可能丢失")
        self._pending_persist_tasks.clear()

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
            conversation_summary=meta.get("conversation_summary", ""),
            summary_turn_count=meta.get("summary_turn_count", 0),
            last_summarized_turn_id=meta.get("last_summarized_turn_id", ""),
            # 坐席辅助引擎层字段（从 Redis 原始 JSON 读取，patch_state 写入的值不会丢失）
            intent_stack=[IntentLabel(i) if isinstance(i, str) else i for i in meta.get("intent_stack", [])],
            entity_pool=[Entity(**e) for e in meta.get("entity_pool", [])],
            emotion_vector=meta.get("emotion_vector"),
            suppress_flag=meta.get("suppress_flag", False),
            node_position=meta.get("node_position", ""),
            risk_pending_audit=meta.get("risk_pending_audit", False),
            agent_id=meta.get("agent_id"),
            transfer_reason=meta.get("transfer_reason"),
            transfer_summary=meta.get("transfer_summary"),
            created_at=datetime.fromisoformat(meta["created_at"]) if meta.get("created_at") else datetime.now(),
            last_active_at=datetime.fromisoformat(meta["last_active_at"])
            if meta.get("last_active_at")
            else datetime.now(),
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
            raise SessionNotFoundError(session_id)

        # 追加对话历史到 Redis List
        turn_json = turn.model_dump_json()
        await self._redis.rpush(self._history_key(session_id), turn_json)

        # 保持历史窗口
        history_len = await self._redis.llen(self._history_key(session_id))
        if history_len > self._max_turns:
            await self._redis.ltrim(self._history_key(session_id), -self._max_turns, -1)

        # 刷新 history key TTL（与 meta key 同步过期，防止内存泄漏）
        await self._redis.expire(self._history_key(session_id), self._ttl)

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

        # 更新实体池：从 turn 中提取实体，增量合并到 last_entities
        # 银行客服关键需求：即使对话历史被裁剪，实体（卡号、金额）仍保留
        if hasattr(turn, "entities") and turn.entities:
            existing_keys = {f"{e.entity_type}:{e.value}" for e in state.last_entities}
            for entity in turn.entities:
                key = f"{entity.entity_type}:{entity.value}"
                if key not in existing_keys:
                    state.last_entities.append(entity)
                    existing_keys.add(key)
            # 保留最近 50 个实体，防止无限增长
            if len(state.last_entities) > 50:
                state.last_entities = state.last_entities[-50:]

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
            raise SessionNotFoundError(session_id)

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
                raise InvalidTransitionError(
                    f"session={session_id} " f"{state.sub_phase.value} → {new_sub_phase.value}"
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

        # CAS 写入，冲突时重新加载并重试一次
        try:
            await self._save_meta(state)
        except InvalidTransitionError:
            # CAS 冲突：重新加载最新状态，合并阶段变更后重试
            logger.warning("transition_phase CAS 冲突, 重试: session=%s", session_id)
            latest = await self.get_session(session_id)
            if latest is None:
                raise SessionNotFoundError(session_id)
            latest.current_phase = new_phase
            latest.sub_phase = new_sub_phase
            latest.last_active_at = datetime.now()
            if new_phase == SessionPhase.AGENT and reason:
                latest.transfer_reason = reason
            if new_phase == SessionPhase.ENDED:
                latest.end_reason = reason or latest.end_reason
            await self._save_meta(latest)
            state = latest
        logger.info(
            "会话 %s 阶段切换: %s:%s → %s:%s (原因: %s)",
            session_id,
            old_phase.value,
            old_sub.value if old_sub else "-",
            new_phase.value,
            new_sub_phase.value if new_sub_phase else "-",
            reason,
        )

        # 记录 Prometheus 指标
        SESSION_TRANSITIONS.labels(
            from_phase=old_phase.value,
            from_sub=old_sub.value if old_sub else "",
            to_phase=new_phase.value,
            to_sub=new_sub_phase.value if new_sub_phase else "",
            reason=reason,
        ).inc()
        if old_sub is not None:
            elapsed = (datetime.now() - state.last_active_at).total_seconds()
            SESSION_PHASE_DURATION.labels(sub_phase=old_sub.value).observe(max(elapsed, 0))

        # 启动/取消超时守卫
        if self._timeout_manager:
            if new_phase == SessionPhase.ENDED:
                self._timeout_manager.cancel_guard(session_id)
            elif new_sub_phase:
                self._timeout_manager.start_guard(session_id, new_sub_phase)

        # 会话结束时异步持久化对话记录到 PostgreSQL（合规审计）
        if new_phase == SessionPhase.ENDED and self._db_session_factory:
            task = asyncio.create_task(self.persist_dialogue(session_id, self._db_session_factory))
            self._pending_persist_tasks.add(task)
            task.add_done_callback(self._pending_persist_tasks.discard)

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

    async def persist_dialogue(self, session_id: str, db_session_factory: Any = None) -> int:
        """会话结束时异步落库对话记录到 PostgreSQL

        满足银行合规审计要求（对话记录保存 5-7 年）。
        落库后不清除 Redis 缓存（由 TTL 自然过期）。

        Args:
            session_id: 会话 ID
            db_session_factory: SQLAlchemy async session factory

        Returns:
            持久化的对话轮次数
        """
        if db_session_factory is None:
            return 0

        turns = await self._load_history(session_id)
        if not turns:
            return 0

        # 读取会话元信息补充 customer_id / channel_type
        meta_json = await self._redis.get(self._meta_key(session_id))
        customer_id = None
        channel_type = "web"
        if meta_json:
            meta = json.loads(meta_json)
            customer_id = meta.get("customer_id")
            channel_type = meta.get("channel_type", "web")

        try:
            from smartcs.shared.orm_models import DialogueLog

            async with db_session_factory() as db:
                for turn in turns:
                    log_entry = DialogueLog(
                        session_id=session_id,
                        turn_id=turn.turn_id,
                        speaker=turn.speaker,
                        content=turn.content,
                        intent=turn.intent.value if turn.intent else None,
                        confidence=turn.confidence,
                        entities=[e.model_dump() for e in turn.entities] if turn.entities else [],
                        response_source=turn.response_source or None,
                        retrieval_context=turn.retrieval_context or None,
                        emotion_label=turn.emotion_label.value if turn.emotion_label else None,
                        emotion_score=turn.emotion_score,
                        timestamp=turn.timestamp,
                        customer_id=customer_id,
                        channel_type=channel_type,
                    )
                    db.add(log_entry)
                await db.commit()

            logger.info("对话记录已持久化: session=%s turns=%d", session_id, len(turns))
            return len(turns)
        except Exception:
            logger.exception("对话记录持久化失败: session=%s", session_id)
            return 0

    # ── 内部方法 ──

    async def _save_meta(self, state: SessionState) -> None:
        """保存会话元信息到 Redis（CAS 乐观锁保护）

        使用 Redis Lua 脚本实现 compare-and-swap:
        - 读取当前 version，与 state.version 比较
        - 匹配则写入并递增 version
        - 不匹配则说明并发修改，重新加载后重试

        这解决了原来盲写（blind write）导致并发更新丢失数据的问题。
        """
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
            "conversation_summary": state.conversation_summary,
            "summary_turn_count": state.summary_turn_count,
            "last_summarized_turn_id": state.last_summarized_turn_id,
            # 坐席辅助引擎层字段（全量序列化，防止 CAS SET 覆写时擦除 patch_state 写入的值）
            "intent_stack": [i.value if hasattr(i, "value") else i for i in state.intent_stack],
            "entity_pool": [e.model_dump() if hasattr(e, "model_dump") else e for e in state.entity_pool],
            "emotion_vector": state.emotion_vector,
            "suppress_flag": state.suppress_flag,
            "node_position": state.node_position,
            "risk_pending_audit": state.risk_pending_audit,
            "agent_id": state.agent_id,
            "transfer_reason": state.transfer_reason,
            "transfer_summary": state.transfer_summary,
            "created_at": state.created_at.isoformat(),
            "last_active_at": state.last_active_at.isoformat(),
            "version": state.version,
        }

        # CAS 写入: version 匹配时才更新
        expected_version = state.version
        state.version = expected_version + 1
        meta["version"] = state.version

        # 使用 Lua 脚本实现原子 CAS
        cas_script = """
        local key = KEYS[1]
        local expected = ARGV[1]
        local new_value = ARGV[2]
        local ttl = ARGV[3]

        local raw = redis.call('GET', key)
        if raw then
            local current = cjson.decode(raw)
            if current.version ~= tonumber(expected) then
                return 0
            end
        end

        redis.call('SET', key, new_value, 'EX', ttl)
        return 1
        """

        key = self._meta_key(state.session_id)
        meta_json = json.dumps(meta, ensure_ascii=False, default=str)

        # 尝试 CAS 写入，失败时重试一次（重新加载 + 合并）
        for attempt in range(2):
            result = await self._redis.eval(
                cas_script,
                1,
                key,
                str(expected_version),
                meta_json,
                str(self._ttl),
            )
            if result == 1:
                return

            # CAS 失败: 并发修改，重新加载并合并
            logger.debug("_save_meta CAS 冲突: session=%s attempt=%d", state.session_id, attempt)
            if attempt == 0:
                # 重新读取当前状态，合并变更
                raw = await self._redis.get(key)
                if raw:
                    current = json.loads(raw)
                    state.version = current.get("version", 1)
                    expected_version = state.version
                    state.version = expected_version + 1
                    meta["version"] = state.version
                    continue

        # 两次 CAS 都失败，抛异常而非盲写（盲写会覆盖并发修改导致数据丢失）
        logger.error("_save_meta CAS 两次失败: session=%s version=%d", state.session_id, expected_version)
        raise InvalidTransitionError(f"session={state.session_id} CAS conflict: version mismatch after retries")

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

    # ── 统一状态层：CAS 读写（替代 StateManager）──

    async def _ensure_script(self) -> str:
        """加载 CAS Lua 脚本并缓存 SHA"""
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(_CAS_WRITE_SCRIPT)
        return self._script_sha

    async def read_state(self, session_id: str) -> dict[str, Any] | None:
        """读取会话原始状态字典（含 version + 坐席辅助引擎扩展字段）

        替代 StateManager.read_state()，数据源为同一个 meta key。
        """
        raw = await self._redis.get(self._meta_key(session_id))
        if raw is None:
            return None
        return json.loads(raw)

    async def patch_state(
        self,
        session_id: str,
        expected_version: int,
        patches: dict[str, Any],
        *,
        writer: str = "",
        max_retries: int = 1,
    ) -> dict[str, Any]:
        """CAS 原子写入部分状态字段

        替代 StateManager.cas_write()，操作同一个 meta key。
        支持字段级合并规则：增量合并 / 单向门 / 全量覆写。

        Returns:
            {"ok": True, "new_version": int} 或 {"ok": False, "current_version": int}
        """
        current_version = expected_version

        for attempt in range(max_retries + 1):
            current = await self.read_state(session_id)
            if current is None:
                return {"ok": False, "current_version": 0, "reason": "not_found"}

            transformed = self._apply_merge_rules(current, patches)
            sha = await self._ensure_script()
            now_iso = datetime.now(UTC).isoformat()
            patch_json = json.dumps(transformed, ensure_ascii=False, default=str)

            result = await self._redis.evalsha(
                sha,
                1,
                self._meta_key(session_id),
                str(current_version),
                patch_json,
                writer,
                str(self._ttl),
                now_iso,
            )

            if isinstance(result, bytes):
                result = json.loads(result.decode())
            elif isinstance(result, str):
                result = json.loads(result)

            if result.get("ok"):
                logger.debug(
                    "CAS 写入成功: session=%s version=%d→%d writer=%s",
                    session_id,
                    current_version,
                    result["new_version"],
                    writer,
                )
                return {"ok": True, "new_version": result["new_version"]}

            current_version = result.get("current_version", current_version)
            if attempt < max_retries:
                continue

        return {"ok": False, "current_version": current_version}

    def _apply_merge_rules(self, current: dict, patches: dict) -> dict[str, Any]:
        """字段级合并规则（与 StateManager 一致）

        - intent_stack / entity_pool: 增量合并（去重）
        - suppress_flag: 单向门 false→true
        - 其他字段: 全量覆写
        """
        adjusted: dict[str, Any] = {}

        for field, value in patches.items():
            if field == "suppress_flag" and value is False and "suppress_force_clear" in patches:
                adjusted[field] = value
                continue
            if field == "suppress_force_clear":
                continue

            if field in _ONE_WAY_GATE_FIELDS:
                current_val = current.get(field, False)
                if not (current_val is True and value is False):
                    adjusted[field] = value
                else:
                    logger.debug("suppress_flag 单向门阻止")

            elif field == "intent_stack":
                current_list: list = current.get(field, [])
                if isinstance(value, list):
                    existing_set = set(str(item) for item in current_list)
                    for item in value:
                        if str(item) not in existing_set:
                            current_list.append(item)
                            existing_set.add(str(item))
                adjusted[field] = current_list

            elif field == "entity_pool":
                current_entities: list[dict] = current.get(field, [])
                if isinstance(value, list):
                    entity_index: dict[str, dict] = {}
                    for entity in current_entities:
                        key = f"{entity.get('entity_type', '')}:{entity.get('value', '')}"
                        entity_index[key] = entity
                    for entity in value:
                        if isinstance(entity, dict):
                            key = f"{entity.get('entity_type', '')}:{entity.get('value', '')}"
                            entity_index[key] = entity
                    adjusted[field] = list(entity_index.values())
                else:
                    adjusted[field] = value

            else:
                adjusted[field] = value

        return adjusted
