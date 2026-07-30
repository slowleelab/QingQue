"""工具调用编排器单元测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.bot.tool_executor import ToolCallingExecutor, ToolExecutionResult, detect_confirmation
from smartcs.services.common.llm import ToolCall, ToolCallResult
from smartcs.shared.config import MCPSettings

# ── detect_confirmation ──


@pytest.mark.parametrize(
    "text,expected",
    [
        ("确认", "confirm"),
        ("好的，继续", "confirm"),
        ("可以办理", "confirm"),
        ("取消", "cancel"),
        ("不用了", "cancel"),
        ("不确认", "cancel"),
        ("不办理了", "cancel"),
        ("今天天气怎么样", "unclear"),
        ("", "unclear"),
    ],
)
def test_detect_confirmation(text, expected):
    assert detect_confirmation(text) == expected


# ── ToolCallingExecutor ──


def _make_executor(mcp=None, llm=None, settings=None, audit_factory=None):
    mcp = mcp or MagicMock()
    mcp.to_openai_tools.return_value = [{"type": "function", "function": {"name": "query_balance"}}]
    llm = llm or MagicMock()
    settings = settings or MCPSettings(enabled=True, max_tool_iterations=5, confirmation_ttl_seconds=300)
    return ToolCallingExecutor(mcp_client=mcp, llm_client=llm, audit_session_factory=audit_factory, settings=settings)


class TestRunConversation:
    async def test_no_tool_calls_returns_llm_text(self):
        """LLM 未请求工具时直接返回文本"""
        llm = MagicMock()
        llm.chat_with_tools = AsyncMock(return_value=ToolCallResult(content="您好，请问需要什么帮助？"))
        ex = _make_executor(llm=llm)

        result = await ex.run_conversation(
            system_prompt="sys", user_input="hi", history=[], session_id="s1", actor_id="c1"
        )
        assert result.source == "llm"
        assert "帮助" in result.content
        assert result.pending_action is None

    async def test_non_sensitive_tool_executes_then_answers(self):
        """非敏感工具：执行 → 回喂 → 最终答复"""
        mcp = MagicMock()
        mcp.to_openai_tools.return_value = [{"type": "function", "function": {"name": "query_balance"}}]
        mcp.is_sensitive.return_value = False
        mcp.call_tool = AsyncMock(return_value={"is_error": False, "content": "余额 100 元"})

        llm = MagicMock()
        llm.chat_with_tools = AsyncMock(
            side_effect=[
                ToolCallResult(
                    tool_calls=[ToolCall(id="t1", name="query_balance", arguments={"card": "1234"})],
                    raw_message={"role": "assistant", "content": "", "tool_calls": []},
                ),
                ToolCallResult(content="您的余额是 100 元。"),
            ]
        )
        ex = _make_executor(mcp=mcp, llm=llm)

        result = await ex.run_conversation(
            system_prompt="sys", user_input="查余额", history=[], session_id="s1", actor_id="c1"
        )
        assert result.source == "llm"
        assert "100" in result.content
        assert result.executed_tools == ["query_balance"]
        mcp.call_tool.assert_awaited_once()

    async def test_sensitive_tool_short_circuits_to_pending(self):
        """敏感工具：不执行，返回待确认"""
        mcp = MagicMock()
        mcp.to_openai_tools.return_value = [{"type": "function", "function": {"name": "card_loss"}}]
        mcp.is_sensitive.return_value = True
        mcp.get_tool.return_value = MagicMock(description="银行卡挂失")
        mcp.call_tool = AsyncMock()

        llm = MagicMock()
        llm.chat_with_tools = AsyncMock(
            return_value=ToolCallResult(
                tool_calls=[ToolCall(id="t1", name="card_loss", arguments={"card": "1234"})],
                raw_message={"role": "assistant", "content": "", "tool_calls": []},
            )
        )
        ex = _make_executor(mcp=mcp, llm=llm)

        result = await ex.run_conversation(
            system_prompt="sys", user_input="挂失", history=[], session_id="s1", actor_id="c1"
        )
        assert result.pending_action is not None
        assert result.pending_action.tool_name == "card_loss"
        assert "确认" in result.content
        mcp.call_tool.assert_not_awaited()  # 敏感工具未执行

    async def test_loop_guard_raises(self):
        """LLM 持续请求工具应在上限处抛出"""
        mcp = MagicMock()
        mcp.to_openai_tools.return_value = [{"type": "function", "function": {"name": "query_balance"}}]
        mcp.is_sensitive.return_value = False
        mcp.call_tool = AsyncMock(return_value={"is_error": False, "content": "x"})

        llm = MagicMock()
        llm.chat_with_tools = AsyncMock(
            return_value=ToolCallResult(
                tool_calls=[ToolCall(id="t1", name="query_balance", arguments={})],
                raw_message={"role": "assistant", "content": "", "tool_calls": []},
            )
        )
        ex = _make_executor(mcp=mcp, llm=llm, settings=MCPSettings(enabled=True, max_tool_iterations=3))

        with pytest.raises(RuntimeError, match="最大轮数"):
            await ex.run_conversation(
                system_prompt="sys", user_input="x", history=[], session_id="s1", actor_id="c1"
            )

    async def test_llm_exception_propagates(self):
        """LLM 异常向上抛出，由调用方降级"""
        llm = MagicMock()
        llm.chat_with_tools = AsyncMock(side_effect=RuntimeError("llm down"))
        ex = _make_executor(llm=llm)
        with pytest.raises(RuntimeError, match="llm down"):
            await ex.run_conversation(
                system_prompt="sys", user_input="x", history=[], session_id="s1", actor_id="c1"
            )

    async def test_tool_names_passthrough_to_openai_tools(self):
        """渐进式暴露：tool_names 原样透传给 to_openai_tools(names)"""
        mcp = MagicMock()
        mcp.to_openai_tools.return_value = [{"type": "function", "function": {"name": "query_card_bill"}}]
        llm = MagicMock()
        llm.chat_with_tools = AsyncMock(return_value=ToolCallResult(content="您好"))
        ex = _make_executor(mcp=mcp, llm=llm)

        names = ["query_card_bill", "query_bill_detail"]
        await ex.run_conversation(
            system_prompt="sys",
            user_input="查账单",
            history=[],
            session_id="s1",
            actor_id="c1",
            tool_names=names,
        )
        mcp.to_openai_tools.assert_called_once_with(names)

    async def test_tool_names_default_none_exposes_all(self):
        """未传 tool_names 时以 None 调用 to_openai_tools（暴露全量，零回归）"""
        mcp = MagicMock()
        mcp.to_openai_tools.return_value = []
        llm = MagicMock()
        llm.chat_with_tools = AsyncMock(return_value=ToolCallResult(content="您好"))
        ex = _make_executor(mcp=mcp, llm=llm)

        await ex.run_conversation(
            system_prompt="sys", user_input="hi", history=[], session_id="s1", actor_id="c1"
        )
        mcp.to_openai_tools.assert_called_once_with(None)


class TestExecuteConfirmedAction:
    async def test_executes_pending_then_answers(self):
        """确认后执行暂存工具并生成最终答复"""
        from smartcs.shared.models import PendingAction

        mcp = MagicMock()
        mcp.to_openai_tools.return_value = []
        mcp.call_tool = AsyncMock(return_value={"is_error": False, "content": "挂失成功"})

        llm = MagicMock()
        llm.chat_with_tools = AsyncMock(return_value=ToolCallResult(content="已为您完成挂失。"))
        ex = _make_executor(mcp=mcp, llm=llm)

        pending = PendingAction(tool_name="card_loss", arguments={"card": "1234"}, tool_call_id="t1")
        result = await ex.execute_confirmed_action(
            pending=pending, system_prompt="sys", history=[], session_id="s1", actor_id="c1"
        )
        assert "挂失" in result.content
        assert "card_loss" in result.executed_tools
        mcp.call_tool.assert_awaited_once_with("card_loss", {"card": "1234"})


class TestAuditOnExecution:
    async def test_audit_written_on_tool_call(self):
        """工具执行应写审计（脱敏）"""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__.return_value = mock_session

        mcp = MagicMock()
        mcp.to_openai_tools.return_value = [{"type": "function", "function": {"name": "query_balance"}}]
        mcp.is_sensitive.return_value = False
        mcp.call_tool = AsyncMock(return_value={"is_error": False, "content": "余额 100 元"})

        llm = MagicMock()
        llm.chat_with_tools = AsyncMock(
            side_effect=[
                ToolCallResult(
                    tool_calls=[ToolCall(id="t1", name="query_balance", arguments={})],
                    raw_message={"role": "assistant", "content": "", "tool_calls": []},
                ),
                ToolCallResult(content="余额 100 元。"),
            ]
        )
        ex = _make_executor(mcp=mcp, llm=llm, audit_factory=mock_factory)

        await ex.run_conversation(
            system_prompt="sys", user_input="查余额", history=[], session_id="s1", actor_id="c1"
        )
        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()


def test_tool_execution_result_defaults():
    r = ToolExecutionResult(content="x", source="llm")
    assert r.pending_action is None
    assert r.executed_tools == []
