"""工具层端到端集成测试

用参考 MCP Server + 进程内内存传输，驱动真实 ``ClientSession`` ↔ ``MCPToolClient``
↔ ``ToolCallingExecutor``，在不依赖网络/Higress 的前提下验证 P0 工具层三条主链路：

1. 非敏感工具：LLM 请求 → 真实 round-trip 执行 → 回喂 → 最终答复
2. 敏感工具：短路为 ``pending_action`` → 用户确认后真实执行
3. schema / 敏感性：从参考 server 拉取的注解正确映射为 OpenAI tools 与敏感判定
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect_in_memory

from lumio.services.common.llm import ToolCall, ToolCallResult
from lumio.services.common.mcp_client import MCPToolClient
from lumio.services.tools.reference_server import build_reference_server
from lumio.shared.config import MCPSettings


class _ScriptedLLM:
    """按脚本顺序返回 ToolCallResult 的假 LLM（duck-typed，仅需 chat_with_tools）"""

    def __init__(self, script: list[ToolCallResult]) -> None:
        self._script = list(script)
        self._idx = 0
        self.calls = 0

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **_: Any
    ) -> ToolCallResult:
        self.calls += 1
        result = self._script[self._idx]
        self._idx += 1
        return result


def _tool_call_result(call_id: str, name: str, arguments: dict[str, Any]) -> ToolCallResult:
    """构造一个请求调用工具的 ToolCallResult（含回喂所需 raw_message）"""
    return ToolCallResult(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        raw_message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
                }
            ],
        },
    )


def _final_answer(text: str) -> ToolCallResult:
    return ToolCallResult(content=text, tool_calls=[], raw_message={"role": "assistant", "content": text})


async def _make_client(session: Any) -> MCPToolClient:
    client = MCPToolClient(MCPSettings(enabled=True, sensitive_tools=[]))
    await client.use_session(session)
    return client


class TestToolLayerE2E:
    """基于真实内存 round-trip 的端到端验证"""

    async def test_tool_schema_and_sensitivity_from_reference_server(self) -> None:
        server = build_reference_server()
        async with connect_in_memory(server._mcp_server) as session:
            await session.initialize()
            client = await _make_client(session)

            tools = client.to_openai_tools()
            names = {t["function"]["name"] for t in tools}
            assert {"query_card_bill", "query_points", "apply_bill_installment"} <= names

            # 只读查询非敏感；destructiveHint 写操作敏感
            assert client.is_sensitive("query_card_bill") is False
            assert client.is_sensitive("query_points") is False
            assert client.is_sensitive("apply_bill_installment") is True
            assert client.is_sensitive("adjust_temp_credit_limit") is True

            # schema 含入参属性
            bill_tool = next(t for t in tools if t["function"]["name"] == "query_card_bill")
            assert "card_no" in bill_tool["function"]["parameters"]["properties"]

    async def test_to_openai_tools_progressive_disclosure(self) -> None:
        """渐进式暴露接缝：names=None 全量（零回归），给定名单则按序筛子集"""
        server = build_reference_server()
        async with connect_in_memory(server._mcp_server) as session:
            await session.initialize()
            client = await _make_client(session)

            full = client.to_openai_tools()
            # None 与不传参行为一致，且为全量
            assert client.to_openai_tools(names=None) == full
            assert len(full) >= 5

            # 仅暴露子集：只返回命中的工具，保持缓存原有顺序
            subset = client.to_openai_tools(names=["query_points", "query_card_bill"])
            subset_names = [t["function"]["name"] for t in subset]
            assert set(subset_names) == {"query_points", "query_card_bill"}
            assert subset_names == [n for n in (t["function"]["name"] for t in full) if n in subset_names]

            # 空名单 → 不暴露任何工具；未知名 → 忽略
            assert client.to_openai_tools(names=[]) == []
            assert client.to_openai_tools(names=["__nonexistent__"]) == []

    async def test_call_tool_real_round_trip(self) -> None:
        server = build_reference_server()
        async with connect_in_memory(server._mcp_server) as session:
            await session.initialize()
            client = await _make_client(session)

            result = await client.call_tool("query_card_bill", {"card_no": "6225880012346780", "month": "2026-07"})
            assert result["is_error"] is False
            assert "3288.5" in result["content"] or "3288" in result["content"]

    async def test_nonsensitive_tool_flow_produces_final_answer(self) -> None:
        from lumio.services.bot.tool_executor import ToolCallingExecutor

        server = build_reference_server()
        async with connect_in_memory(server._mcp_server) as session:
            await session.initialize()
            client = await _make_client(session)

            llm = _ScriptedLLM(
                [
                    _tool_call_result("c1", "query_card_bill", {"card_no": "6225880012346780", "month": "2026-07"}),
                    _final_answer("您本期账单应还 3288.50 元，最低还款 328.85 元，还款日 2026-08-15。"),
                ]
            )
            executor = ToolCallingExecutor(client, llm, None, client._settings)  # type: ignore[arg-type]

            res = await executor.run_conversation(
                system_prompt="你是信用卡客服助手",
                user_input="帮我查一下本期账单",
                history=[],
                session_id="sess-e2e-1",
                actor_id="cust-1",
            )

            assert res.pending_action is None
            assert res.source == "llm"
            assert "query_card_bill" in res.executed_tools
            assert "3288" in res.content
            assert llm.calls == 2

    async def test_sensitive_tool_short_circuits_to_pending_then_confirms(self) -> None:
        from lumio.services.bot.tool_executor import ToolCallingExecutor

        server = build_reference_server()
        async with connect_in_memory(server._mcp_server) as session:
            await session.initialize()
            client = await _make_client(session)

            # 第一轮：LLM 请求敏感工具 → 应短路为 pending（不执行）
            llm = _ScriptedLLM(
                [
                    _tool_call_result(
                        "c2", "apply_bill_installment", {"card_no": "6225880012346780", "amount": 3000, "periods": 6}
                    )
                ]
            )
            executor = ToolCallingExecutor(client, llm, None, client._settings)  # type: ignore[arg-type]

            res = await executor.run_conversation(
                system_prompt="你是信用卡客服助手",
                user_input="把这笔 3000 元分 6 期",
                history=[],
                session_id="sess-e2e-2",
                actor_id="cust-1",
            )
            assert res.pending_action is not None
            assert res.pending_action.tool_name == "apply_bill_installment"
            assert "确认" in res.pending_action.confirm_prompt
            assert "apply_bill_installment" not in res.executed_tools  # 未执行

            # 第二轮：用户确认 → 真实执行敏感工具 → 生成最终答复
            confirm_llm = _ScriptedLLM([_final_answer("已为您办理 6 期账单分期，受理成功。")])
            confirm_exec = ToolCallingExecutor(client, confirm_llm, None, client._settings)  # type: ignore[arg-type]
            confirmed = await confirm_exec.execute_confirmed_action(
                pending=res.pending_action,
                system_prompt="你是信用卡客服助手",
                history=[],
                session_id="sess-e2e-2",
                actor_id="cust-1",
            )
            assert confirmed.pending_action is None
            assert "apply_bill_installment" in confirmed.executed_tools
            assert "分期" in confirmed.content

    async def test_disabled_client_has_no_tools(self) -> None:
        # MCP 关闭时：即便未连接，list/to_openai_tools 为空 → 编排层回落（零回归契约）
        client = MCPToolClient(MCPSettings(enabled=False))
        assert await client.list_tools() == []
        assert client.to_openai_tools() == []


@pytest.mark.asyncio
async def test_reference_server_builds_expected_tool_set() -> None:
    server = build_reference_server()
    async with connect_in_memory(server._mcp_server) as session:
        await session.initialize()
        listed = await session.list_tools()
        names = {t.name for t in listed.tools}
        assert names == {
            "query_card_bill",
            "query_points",
            "query_installment_offer",
            "apply_bill_installment",
            "adjust_temp_credit_limit",
        }
