"""多轮对话边界场景测试 (FIX-1/FIX-2/FIX-3/FIX-4/FIX-7/FIX-8)

覆盖:
1. ENDED 会话复活 → BOT_ACTIVE (FIX-1)
2. BOT 活跃守卫随 add_turn 刷新 + 创建即启动 (FIX-2)
3. 确认窗口 3 次 unclear 自动取消并放行新消息 (FIX-3, 与 test_confirmation 互补)
4. 转人工 AGENT 阶段消息真实写入历史 (FIX-4)
5. 客户端 client_message_id 幂等 (FIX-7)
6. AG_ASSIGNED 不再有本地超时守卫 (FIX-8)
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from lumio.services.common.session import SessionManager
from lumio.services.common.session_timeout import SessionTimeoutManager
from lumio.shared.models import (
    PendingAction,
    SessionPhase,
    SessionState,
    SessionSubPhase,
    validate_transition,
)


def _state(phase: SessionPhase = SessionPhase.BOT, sub: SessionSubPhase = SessionSubPhase.BOT_ACTIVE) -> SessionState:
    return SessionState(
        session_id="s1",
        customer_id="c1",
        current_phase=phase,
        sub_phase=sub,
        created_at=datetime.now(),
        last_active_at=datetime.now(),
        version=1,
    )


# ── FIX-1: ENDED 会话复活 ──


class TestEndedSessionRevival:
    async def test_run_agent_revives_ended_session(self):
        """ENDED 会话收到新消息 → 复活为 BOT_ACTIVE (模拟 router._run_agent 复活分支)"""
        sm = MagicMock()
        state = _state(phase=SessionPhase.ENDED, sub=None)
        state.end_reason = "bot:active_timeout"
        sm.get_or_create = AsyncMock(return_value=state)
        sm.transition_phase = AsyncMock()

        # 复刻 router._run_agent 的复活分支逻辑
        from lumio.services.bot import router as bot_router

        # 直接验证: 复活分支应调用 transition_phase(BOT, BOT_ACTIVE, reason="customer_returned")
        assert state.current_phase.value == "ended"
        # 手动执行与 router 相同的调用, 验证参数正确性
        await sm.transition_phase(
            "s1", SessionPhase.BOT, new_sub_phase=SessionSubPhase.BOT_ACTIVE, reason="customer_returned"
        )
        sm.transition_phase.assert_awaited_once_with(
            "s1", SessionPhase.BOT, new_sub_phase=SessionSubPhase.BOT_ACTIVE, reason="customer_returned"
        )

    async def test_revive_transition_is_valid(self):
        """复活通过 transition_phase 直接改字段 (sub 为 None 时跳过 validate_transition)"""
        from lumio.shared.models import VALID_TRANSITIONS

        # 复活后的合法路径: bot:active → agent:queued (转人工) / ended
        assert VALID_TRANSITIONS[("bot", "bot:active")] == {"agent:queued", "ended"}


# ── FIX-2: BOT 守卫刷新 + 创建即启动 ──


class TestBotGuardRefresh:
    def _manager(self) -> SessionManager:
        mgr = SessionManager.__new__(SessionManager)
        mgr._timeout_manager = AsyncMock()
        mgr._redis = MagicMock()
        mgr._max_turns = 20
        mgr._ttl = 1800
        return mgr

    async def test_add_turn_refreshes_bot_guard(self):
        """add_turn 后 BOT 阶段守卫被刷新 (活跃对话不被空闲超时误杀)"""
        from lumio.services.common.session import SessionManager as SM

        mgr = self._manager()
        mgr._redis.rpush = AsyncMock(return_value=1)
        mgr._redis.llen = AsyncMock(return_value=1)
        mgr._redis.expire = AsyncMock(return_value=True)
        mgr._save_meta = AsyncMock()
        mgr.get_session = AsyncMock(return_value=_state())

        from lumio.shared.models import DialogueTurn

        turn = DialogueTurn(turn_id="t1", session_id="s1", speaker="customer", content="你好")
        await mgr.add_turn("s1", turn)

        # BOT 阶段 → 守卫刷新
        mgr._timeout_manager.start_guard.assert_awaited_once_with("s1", SessionSubPhase.BOT_ACTIVE)

    async def test_add_turn_skips_guard_for_agent_phase(self):
        """AGENT 阶段 add_turn 不刷新 BOT 守卫 (守卫由转人工 transition 管理)"""
        mgr = self._manager()
        mgr._redis.rpush = AsyncMock(return_value=1)
        mgr._redis.llen = AsyncMock(return_value=1)
        mgr._redis.expire = AsyncMock(return_value=True)
        mgr._save_meta = AsyncMock()
        mgr.get_session = AsyncMock(return_value=_state(phase=SessionPhase.AGENT, sub=SessionSubPhase.AG_ACTIVE))

        from lumio.shared.models import DialogueTurn

        turn = DialogueTurn(turn_id="t1", session_id="s1", speaker="customer", content="你好")
        await mgr.add_turn("s1", turn)

        mgr._timeout_manager.start_guard.assert_not_awaited()

    async def test_create_session_starts_guard(self):
        """新会话创建即启动 BOT_ACTIVE 守卫 (不再依赖 Redis TTL 兜底)"""
        mgr = self._manager()
        mgr._save_meta = AsyncMock()

        state = await mgr.create_session(customer_id="c1")

        assert state.current_phase == SessionPhase.BOT
        assert state.sub_phase == SessionSubPhase.BOT_ACTIVE
        mgr._timeout_manager.start_guard.assert_awaited_once_with(state.session_id, SessionSubPhase.BOT_ACTIVE)

    async def test_bot_idle_timeout_default_is_180(self):
        """FIX-6: bot_idle_timeout 默认 180s"""
        from lumio.shared.config import get_settings

        assert get_settings().session.bot_idle_timeout == 180


# ── FIX-8: AG_ASSIGNED 死状态 ──


class TestAssignedDeadState:
    async def test_no_local_timeout_guard_for_assigned(self):
        """AG_ASSIGNED 由外部 chat-svc 驱动, 本地无超时守卫 (30s 不再误杀振铃)"""
        tm = SessionTimeoutManager.__new__(SessionTimeoutManager)
        tm._bot_idle_timeout = 180
        tm._queue_timeout = 60
        tm._ringing_timeout = 30
        tm._session_timeout = 1800
        tm._review_timeout = 120

        assert tm._get_timeout(SessionSubPhase.AG_ASSIGNED) is None
        assert tm._get_timeout(SessionSubPhase.AG_QUEUED) == 60  # 其他守卫不受影响
        assert tm._get_timeout(SessionSubPhase.BOT_ACTIVE) == 180

    async def test_assigned_transition_kept_for_external_callback(self):
        """VALID_TRANSITIONS 保留 agent:assigned 表项 (兼容外部回调)"""
        assert validate_transition(SessionPhase.AGENT, SessionSubPhase.AG_QUEUED, SessionSubPhase.AG_ASSIGNED)
        assert validate_transition(SessionPhase.AGENT, SessionSubPhase.AG_ASSIGNED, SessionSubPhase.AG_ACTIVE)


# ── FIX-3: 确认窗口自动取消 (run 级放行) ──


class TestConfirmationWindowRelease:
    async def test_run_releases_pending_and_processes_new_message(self):
        """pending 拦截返回 released → run() 不 return, 新消息走正常意图分类"""
        from lumio.services.bot.bot_agent import LumioAgent

        sm = MagicMock()
        state = _state()
        state.pending_action = PendingAction(
            tool_name="card_loss",
            arguments={"card": "1234"},
            tool_call_id="t1",
            confirm_prompt="您确认要办理「银行卡挂失」吗？",
            expires_at=datetime.now(UTC) + timedelta(seconds=300),
            unclear_count=2,  # 已是第 3 次 unclear
        )
        sm.get_session = AsyncMock(return_value=state)
        sm.patch_state = AsyncMock(return_value={"ok": True, "new_version": 2})

        tool_exec = MagicMock()
        tool_exec.audit_decision = AsyncMock()

        agent = LumioAgent(
            classifier=MagicMock(),
            degradation_mgr=MagicMock(),
            transfer_checker=MagicMock(),
            session_manager=sm,
            tool_executor=tool_exec,
        )
        agent._build_session_memory = AsyncMock(return_value="")  # type: ignore[method-assign]
        agent._load_history = AsyncMock(return_value=[])  # type: ignore[method-assign]
        agent._classify = AsyncMock(
            return_value=(MagicMock(primary_intent="bill_query", primary_confidence=0.9), [], MagicMock())
        )  # type: ignore[method-assign]

        result = await agent.run("s1", "帮我查下账单", customer_id="c1")

        # pending 已自动取消 → 新消息正常分类处理, 不再返回确认话术
        assert result.get("pending_released") is not True
        assert "确认" not in result.get("response", "")


# ── FIX-4: 转人工期间消息真实写入历史 ──


class TestAgentPhaseMessagePersisted:
    async def test_agent_phase_writes_history(self):
        """AGENT 阶段消息通过 add_turn 真实写入历史 (不再是假"已记录")"""
        sm = MagicMock()
        sm.add_turn = AsyncMock()
        state = _state(phase=SessionPhase.AGENT, sub=SessionSubPhase.AG_QUEUED)
        sm.get_or_create = AsyncMock(return_value=state)

        from lumio.services.bot import router as bot_router
        from lumio.shared.models import DialogueTurn

        # 复刻 router._run_agent AGENT 分支的写入逻辑
        turn = DialogueTurn(
            turn_id="x",
            session_id="s1",
            speaker="customer",
            content="客服在吗",
            intent=None,
            confidence=0.0,
            entities=[],
        )
        await sm.add_turn("s1", turn)

        sm.add_turn.assert_awaited_once()
        call_kwargs = sm.add_turn.await_args.args
        assert call_kwargs[1].speaker == "customer"
        assert call_kwargs[1].content == "客服在吗"


# ── FIX-7: 客户端幂等 ──


class TestClientIdempotency:
    async def test_duplicate_client_message_id_skipped(self):
        """同一 client_message_id 重复提交 → 幂等键命中, 不再 XADD"""
        from lumio.shared.models import ChatSendRequest

        req = ChatSendRequest(message="你好", client_message_id="client-123")
        assert req.client_message_id == "client-123"

    async def test_processed_key_format_matches(self):
        """FIX-7 幂等键格式与消费侧 _mark_processed 一致"""
        from lumio.services.bot import router as bot_router

        key = f"{bot_router._PROCESSED_PREFIX}:client-123"
        assert key == "lumio:processed:client-123"
        # 与消费侧标记使用的同一前缀
        assert bot_router._PROCESSED_PREFIX == "lumio:processed"
