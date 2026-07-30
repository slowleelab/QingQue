"""工具护栏（ToolGuard）单元 + 集成测试

覆盖 P2 三条主链路：
1. 授权白名单 / 额度校验的纯逻辑判定（含默认空配置零回归）
2. 护栏在执行器中拦截：短路、不执行、记录指标 + 审计
3. 确认后重校验（防篡改）与决策审计
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from smartcs.services.bot.tool_executor import ToolCallingExecutor
from smartcs.services.bot.tool_guard import GuardDecision, ToolGuard
from smartcs.services.common.llm import ToolCall, ToolCallResult
from smartcs.shared.config import MCPSettings
from smartcs.shared.models import PendingAction

# ── ToolGuard 纯逻辑 ──


class TestToolGuardAuthorization:
    def test_empty_config_allows_all_zero_regression(self):
        guard = ToolGuard(MCPSettings(enabled=True))
        assert guard.active is False
        d = guard.check("apply_bill_installment", {"amount": 999999}, actor_role="customer")
        assert d.allowed is True

    def test_role_allowed_tool_passes(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_role_allowlist={"customer": ["query_card_bill"]}))
        assert guard.active is True
        assert guard.check("query_card_bill", {}, actor_role="customer").allowed is True

    def test_role_disallowed_tool_denied(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_role_allowlist={"customer": ["query_card_bill"]}))
        d = guard.check("apply_bill_installment", {}, actor_role="customer")
        assert d.allowed is False
        assert d.code == "role_denied"

    def test_unknown_role_denied_when_allowlist_configured(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_role_allowlist={"agent": ["apply_bill_installment"]}))
        d = guard.check("query_card_bill", {}, actor_role="customer")
        assert d.allowed is False
        assert d.code == "role_denied"


class TestToolGuardAmount:
    def test_amount_within_limit_passes(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_amount_limits={"apply_bill_installment": 50000}))
        assert guard.check("apply_bill_installment", {"amount": 3000}, actor_role="customer").allowed is True

    def test_amount_exceeds_limit_denied(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_amount_limits={"apply_bill_installment": 50000}))
        d = guard.check("apply_bill_installment", {"amount": 80000}, actor_role="customer")
        assert d.allowed is False
        assert d.code == "amount_exceeded"

    def test_string_amount_coerced_and_checked(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_amount_limits={"adjust_temp_credit_limit": 20000}))
        d = guard.check("adjust_temp_credit_limit", {"target_limit": "30000"}, actor_role="customer")
        assert d.allowed is False

    def test_non_numeric_amount_ignored(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_amount_limits={"apply_bill_installment": 50000}))
        assert guard.check("apply_bill_installment", {"amount": "N/A"}, actor_role="customer").allowed is True

    def test_tool_without_limit_passes(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_amount_limits={"apply_bill_installment": 50000}))
        assert guard.check("query_card_bill", {"amount": 999999}, actor_role="customer").allowed is True

    def test_authorization_checked_before_amount(self):
        guard = ToolGuard(
            MCPSettings(
                enabled=True,
                tool_role_allowlist={"customer": ["query_card_bill"]},
                tool_amount_limits={"apply_bill_installment": 50000},
            )
        )
        d = guard.check("apply_bill_installment", {"amount": 80000}, actor_role="customer")
        assert d.allowed is False
        assert d.code == "role_denied"  # 授权先失败


# ── 执行器集成 ──


def _executor_with_guard(guard, *, is_sensitive=False, audit_factory=None):
    mcp = MagicMock()
    mcp.to_openai_tools.return_value = [{"type": "function", "function": {"name": "apply_bill_installment"}}]
    mcp.is_sensitive.return_value = is_sensitive
    mcp.call_tool = AsyncMock(return_value={"is_error": False, "content": "受理成功"})
    mcp.get_tool.return_value = MagicMock(description="账单分期办理")
    llm = MagicMock()
    settings = guard._settings
    ex = ToolCallingExecutor(mcp_client=mcp, llm_client=llm, audit_session_factory=audit_factory, settings=settings, guard=guard)
    return ex, mcp, llm


class TestGuardInExecutor:
    async def test_denied_tool_short_circuits_without_execution(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_amount_limits={"apply_bill_installment": 50000}))
        ex, mcp, llm = _executor_with_guard(guard)
        llm.chat_with_tools = AsyncMock(
            return_value=ToolCallResult(
                tool_calls=[ToolCall(id="t1", name="apply_bill_installment", arguments={"amount": 80000, "periods": 6})],
                raw_message={"role": "assistant", "content": "", "tool_calls": []},
            )
        )

        res = await ex.run_conversation(
            system_prompt="sys", user_input="分期 8 万", history=[], session_id="s1", actor_id="c1"
        )
        assert res.source == "guard"
        assert "无法" in res.content
        mcp.call_tool.assert_not_called()  # 未执行

    async def test_allowed_tool_still_executes(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_amount_limits={"apply_bill_installment": 50000}))
        ex, mcp, llm = _executor_with_guard(guard)
        llm.chat_with_tools = AsyncMock(
            side_effect=[
                ToolCallResult(
                    tool_calls=[ToolCall(id="t1", name="apply_bill_installment", arguments={"amount": 3000, "periods": 6})],
                    raw_message={"role": "assistant", "content": "", "tool_calls": []},
                ),
                ToolCallResult(content="已为您办理分期"),
            ]
        )
        res = await ex.run_conversation(
            system_prompt="sys", user_input="分期 3000", history=[], session_id="s1", actor_id="c1"
        )
        assert res.source == "llm"
        mcp.call_tool.assert_awaited_once()

    async def test_confirmed_action_revalidates_and_denies_tampered_amount(self):
        guard = ToolGuard(MCPSettings(enabled=True, tool_amount_limits={"apply_bill_installment": 50000}))
        ex, mcp, llm = _executor_with_guard(guard, is_sensitive=True)
        pending = PendingAction(
            tool_name="apply_bill_installment",
            arguments={"amount": 999999, "periods": 6},  # 篡改为超额
            tool_call_id="t1",
        )
        res = await ex.execute_confirmed_action(
            pending=pending, system_prompt="sys", history=[], session_id="s1", actor_id="c1"
        )
        assert res.source == "guard"
        mcp.call_tool.assert_not_called()  # 重校验拦截，未执行

    async def test_guard_denial_writes_audit(self):
        session = AsyncMock()
        session.add = MagicMock()
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        guard = ToolGuard(MCPSettings(enabled=True, tool_amount_limits={"apply_bill_installment": 50000}))
        ex, mcp, llm = _executor_with_guard(guard, audit_factory=session_factory)
        llm.chat_with_tools = AsyncMock(
            return_value=ToolCallResult(
                tool_calls=[ToolCall(id="t1", name="apply_bill_installment", arguments={"amount": 80000})],
                raw_message={"role": "assistant", "content": "", "tool_calls": []},
            )
        )
        await ex.run_conversation(
            system_prompt="sys", user_input="分期 8 万", history=[], session_id="s1", actor_id="c1"
        )
        session.add.assert_called_once()  # 拒绝被审计


class TestDecisionAudit:
    async def test_audit_decision_writes_record(self):
        session = AsyncMock()
        session.add = MagicMock()
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        guard = ToolGuard(MCPSettings(enabled=True))
        ex, _mcp, _llm = _executor_with_guard(guard, audit_factory=session_factory)
        await ex.audit_decision(
            session_id="s1", actor_id="c1", actor_role="customer", tool_name="apply_bill_installment", decision="cancel"
        )
        session.add.assert_called_once()

    async def test_audit_decision_noop_without_factory(self):
        guard = ToolGuard(MCPSettings(enabled=True))
        ex, _mcp, _llm = _executor_with_guard(guard, audit_factory=None)
        # 无 audit factory 时应静默返回，不抛异常
        await ex.audit_decision(
            session_id="s1", actor_id="c1", actor_role="customer", tool_name="t", decision="confirm"
        )


def test_guard_decision_defaults():
    d = GuardDecision(allowed=True)
    assert d.code == ""
    assert d.reason == ""
