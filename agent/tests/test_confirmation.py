"""Bot Agent 工具确认状态机单元测试

聚焦 _handle_pending_action 的 confirm/cancel/unclear/expired 四条分支，
以及 pending_action 的写入/清除。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from lumio.services.bot.bot_agent import LumioAgent
from lumio.services.bot.tool_executor import ToolExecutionResult
from lumio.shared.models import PendingAction, SessionState


def _make_agent(session_manager, tool_executor):
    # 决策审计为异步方法，确保被 await 时可用（P2 引入）
    if not isinstance(getattr(tool_executor, "audit_decision", None), AsyncMock):
        tool_executor.audit_decision = AsyncMock()
    agent = LumioAgent(
        classifier=MagicMock(),
        degradation_mgr=MagicMock(),
        transfer_checker=MagicMock(),
        session_manager=session_manager,
        tool_executor=tool_executor,
    )
    # 隔离与状态机无关的记忆/历史加载
    agent._build_session_memory = AsyncMock(return_value="")  # type: ignore[method-assign]
    agent._load_history = AsyncMock(return_value=[])  # type: ignore[method-assign]
    return agent


def _state_with_pending(*, expires_delta_seconds: int = 300) -> SessionState:
    pending = PendingAction(
        tool_name="card_loss",
        arguments={"card": "1234"},
        tool_call_id="t1",
        confirm_prompt="您确认要办理「银行卡挂失」吗？回复『确认』继续办理，回复『取消』放弃。",
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_delta_seconds),
    )
    return SessionState(session_id="s1", customer_id="c1", pending_action=pending, version=3)


class TestPendingActionStateMachine:
    async def test_confirm_executes_and_clears(self):
        sm = MagicMock()
        sm.patch_state = AsyncMock(return_value={"ok": True, "new_version": 4})
        tool_exec = MagicMock()
        tool_exec.execute_confirmed_action = AsyncMock(
            return_value=ToolExecutionResult(content="已为您完成挂失。", source="llm")
        )
        agent = _make_agent(sm, tool_exec)
        state = _state_with_pending()

        result = await agent._handle_pending_action("s1", "确认", state, "c1")

        assert "挂失" in result["response"]
        tool_exec.execute_confirmed_action.assert_awaited_once()
        # 清除 pending_action
        sm.patch_state.assert_awaited()
        patches = sm.patch_state.await_args.kwargs["patches"]
        assert patches == {"pending_action": None}

    async def test_cancel_clears_without_executing(self):
        sm = MagicMock()
        sm.patch_state = AsyncMock(return_value={"ok": True, "new_version": 4})
        tool_exec = MagicMock()
        tool_exec.execute_confirmed_action = AsyncMock()
        agent = _make_agent(sm, tool_exec)
        state = _state_with_pending()

        result = await agent._handle_pending_action("s1", "取消", state, "c1")

        assert "取消" in result["response"]
        tool_exec.execute_confirmed_action.assert_not_awaited()
        sm.patch_state.assert_awaited_once()

    async def test_unclear_reasks_without_clearing(self):
        sm = MagicMock()
        sm.patch_state = AsyncMock(return_value={"ok": True, "new_version": 4})
        tool_exec = MagicMock()
        tool_exec.execute_confirmed_action = AsyncMock()
        agent = _make_agent(sm, tool_exec)
        state = _state_with_pending()

        result = await agent._handle_pending_action("s1", "今天天气不错", state, "c1")

        # FIX-3: unclear 不清除 pending, 但会递增 unclear_count (为自动取消逃生路径计数)
        assert state.pending_action.confirm_prompt in result["response"]
        tool_exec.execute_confirmed_action.assert_not_awaited()
        sm.patch_state.assert_awaited_once()
        patches = sm.patch_state.await_args.kwargs["patches"]
        assert patches["pending_action"]["unclear_count"] == 1  # 计数递增, pending 未清除

    async def test_unclear_three_times_auto_cancel_and_release(self):
        """FIX-3: 连续 3 次无法判定 → 自动取消 pending + released 标记, 新消息继续正常处理"""
        sm = MagicMock()
        sm.patch_state = AsyncMock(return_value={"ok": True, "new_version": 4})
        tool_exec = MagicMock()
        tool_exec.audit_decision = AsyncMock()
        tool_exec.execute_confirmed_action = AsyncMock()
        agent = _make_agent(sm, tool_exec)
        # 已计数 2 次, 本次为第 3 次 → 触发自动取消
        state = _state_with_pending()
        state.pending_action.unclear_count = 2

        result = await agent._handle_pending_action("s1", "帮我查下账单", state, "c1")

        assert result.get("pending_released") is True  # run() 据此继续处理新消息
        assert "取消" in result["response"]
        tool_exec.execute_confirmed_action.assert_not_awaited()
        sm.patch_state.assert_awaited_once()
        patches = sm.patch_state.await_args.kwargs["patches"]
        assert patches == {"pending_action": None}  # pending 已清除

    async def test_expired_clears_and_prompts_restart(self):
        sm = MagicMock()
        sm.patch_state = AsyncMock(return_value={"ok": True, "new_version": 4})
        tool_exec = MagicMock()
        tool_exec.execute_confirmed_action = AsyncMock()
        agent = _make_agent(sm, tool_exec)
        state = _state_with_pending(expires_delta_seconds=-10)  # 已过期

        result = await agent._handle_pending_action("s1", "确认", state, "c1")

        assert "超时" in result["response"]
        tool_exec.execute_confirmed_action.assert_not_awaited()
        sm.patch_state.assert_awaited_once()

    async def test_confirm_tool_failure_degrades(self):
        sm = MagicMock()
        sm.patch_state = AsyncMock(return_value={"ok": True, "new_version": 4})
        tool_exec = MagicMock()
        tool_exec.execute_confirmed_action = AsyncMock(side_effect=RuntimeError("tool boom"))
        degrader = MagicMock()
        degrader.hardcoded_fallback.return_value = "抱歉，系统繁忙，请稍后再试。"
        agent = _make_agent(sm, tool_exec)
        agent._degradation_mgr._degrader = degrader

        state = _state_with_pending()
        result = await agent._handle_pending_action("s1", "确认", state, "c1")

        assert result["response_source"] == "fallback"
        sm.patch_state.assert_awaited_once()  # 仍清除 pending


class TestSavePendingAction:
    async def test_save_writes_serialized_pending(self):
        sm = MagicMock()
        sm.get_session = AsyncMock(return_value=SessionState(session_id="s1", version=2))
        sm.patch_state = AsyncMock(return_value={"ok": True, "new_version": 3})
        agent = _make_agent(sm, MagicMock())

        pending = PendingAction(tool_name="card_loss", arguments={"card": "1234"}, tool_call_id="t1")
        await agent._save_pending_action("s1", pending)

        sm.patch_state.assert_awaited_once()
        patches = sm.patch_state.await_args.kwargs["patches"]
        assert patches["pending_action"]["tool_name"] == "card_loss"
