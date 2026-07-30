"""MCPToolClient 路由模式（多后端）单元/集成测试

覆盖：
1. 多后端合并 + 域前缀命名空间（host-facing 名 = prefix + raw_name）
2. `to_openai_tools(names)` 在合并目录上按前缀全名过滤
3. `call_tool(host_name)` 按 name→(server, raw_name) 分发到正确后端，用 raw_name 调用
4. 敏感性判定：destructiveHint 注解、后端 sensitive_tools、全局 sensitive_tools 并集
5. 优雅降级：某后端列举失败仅其工具缺席，不影响其余后端
6. 单后端零回归：backends=[] + use_session 时名字/schema/敏感判定与现状逐字一致
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from mcp.shared.memory import create_connected_server_and_client_session as connect_in_memory

from smartcs.services.common.mcp_client import MCPToolClient
from smartcs.services.tools.reference_server import build_reference_server
from smartcs.shared.config import MCPBackend, MCPSettings


def _make_tool(name: str, description: str = "", *, destructive: bool | None = None, schema: dict | None = None):
    """构造类 mcp.types.Tool 的轻量对象"""
    annotations = SimpleNamespace(destructiveHint=destructive) if destructive is not None else None
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {"card_no": {"type": "string"}}},
        annotations=annotations,
    )


def _mock_session(tools: list, *, call_return: str = "ok", raises_list: bool = False) -> MagicMock:
    """构造带 list_tools / call_tool 的假 ClientSession"""
    session = MagicMock()
    if raises_list:
        session.list_tools = AsyncMock(side_effect=ConnectionError("backend down"))
    else:
        session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=tools))
    session.call_tool = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(text=call_return)],
            structuredContent=None,
            isError=False,
        )
    )
    return session


def _two_backend_settings() -> MCPSettings:
    return MCPSettings(
        enabled=True,
        backends=[
            MCPBackend(name="card", endpoint="http://card/mcp", prefix="card."),
            MCPBackend(name="points", endpoint="http://points/mcp", prefix="pts.", sensitive_tools=["query_transactions"]),
        ],
    )


class TestMergeAndNamespace:
    async def test_merges_tools_with_domain_prefix(self):
        """两后端目录合并，host-facing 名带各自域前缀"""
        session_card = _mock_session(
            [
                _make_tool("query_card_bill", "查账单", destructive=False),
                _make_tool("apply_bill_installment", "办分期", destructive=True),
            ]
        )
        session_points = _mock_session(
            [
                _make_tool("query_points", "查积分", destructive=False),
                _make_tool("query_transactions", "查交易", destructive=False),
            ]
        )
        client = MCPToolClient(_two_backend_settings())
        await client.use_backend_sessions({"card": session_card, "points": session_points})

        names = {t.name for t in await client.list_tools()}
        assert names == {
            "card.query_card_bill",
            "card.apply_bill_installment",
            "pts.query_points",
            "pts.query_transactions",
        }
        # ToolSpec 保留来源后端与原名
        spec = client.get_tool("card.apply_bill_installment")
        assert spec is not None
        assert spec.server == "card"
        assert spec.raw_name == "apply_bill_installment"

    async def test_sensitivity_union_across_sources(self):
        """敏感性 = 注解 destructiveHint ∪ 后端 sensitive_tools ∪ 全局 sensitive_tools"""
        session_card = _mock_session(
            [
                _make_tool("query_card_bill", destructive=False),
                _make_tool("apply_bill_installment", destructive=True),  # 注解敏感
            ]
        )
        session_points = _mock_session(
            [
                _make_tool("query_points", destructive=False),
                _make_tool("query_transactions", destructive=False),  # 后端 sensitive_tools 命中
            ]
        )
        client = MCPToolClient(_two_backend_settings())
        await client.use_backend_sessions({"card": session_card, "points": session_points})

        assert client.is_sensitive("card.query_card_bill") is False
        assert client.is_sensitive("card.apply_bill_installment") is True  # destructiveHint
        assert client.is_sensitive("pts.query_points") is False
        assert client.is_sensitive("pts.query_transactions") is True  # 后端白名单

    async def test_to_openai_tools_filters_by_prefixed_name(self):
        """渐进式暴露：按带前缀全名过滤合并目录"""
        session_card = _mock_session([_make_tool("query_card_bill", destructive=False)])
        session_points = _mock_session([_make_tool("query_points", destructive=False)])
        client = MCPToolClient(_two_backend_settings())
        await client.use_backend_sessions({"card": session_card, "points": session_points})

        subset = client.to_openai_tools(names=["card.query_card_bill"])
        assert [t["function"]["name"] for t in subset] == ["card.query_card_bill"]
        # None → 全量
        assert len(client.to_openai_tools()) == 2


class TestDispatch:
    async def test_call_tool_dispatches_to_correct_backend_with_raw_name(self):
        """call_tool 按分发索引路由到对应后端，并用去前缀的 raw_name 调用"""
        session_card = _mock_session([_make_tool("query_card_bill", destructive=False)], call_return="账单 8650")
        session_points = _mock_session([_make_tool("query_points", destructive=False)], call_return="积分 12000")
        client = MCPToolClient(_two_backend_settings())
        await client.use_backend_sessions({"card": session_card, "points": session_points})

        r1 = await client.call_tool("card.query_card_bill", {"card_no": "6225880000001234"})
        assert "8650" in r1["content"]
        session_card.call_tool.assert_awaited_once()
        assert session_card.call_tool.await_args.args[0] == "query_card_bill"  # raw_name，无前缀
        session_points.call_tool.assert_not_awaited()

        r2 = await client.call_tool("pts.query_points", {"card_no": "6225880000001234"})
        assert "12000" in r2["content"]
        assert session_points.call_tool.await_args.args[0] == "query_points"


class TestGracefulDegradation:
    async def test_failed_backend_tools_absent_others_intact(self):
        """某后端列举失败 → 其工具缺席，其余后端工具与调用不受影响"""
        session_card = _mock_session([_make_tool("query_card_bill", destructive=False)], call_return="账单 8650")
        session_points = _mock_session([], raises_list=True)  # points 后端掉线
        client = MCPToolClient(_two_backend_settings())
        await client.use_backend_sessions({"card": session_card, "points": session_points})

        names = {t.name for t in await client.list_tools()}
        assert names == {"card.query_card_bill"}
        # 存活后端仍可调用
        r = await client.call_tool("card.query_card_bill", {"card_no": "6225880000001234"})
        assert "8650" in r["content"]

    async def test_all_backends_fail_yields_no_tools(self):
        """所有后端列举失败 → 无工具（编排层据此回落）"""
        client = MCPToolClient(_two_backend_settings())
        await client.use_backend_sessions(
            {"card": _mock_session([], raises_list=True), "points": _mock_session([], raises_list=True)}
        )
        assert await client.list_tools() == []
        assert client.to_openai_tools() == []


class TestSingleBackendZeroRegression:
    async def test_use_session_keeps_bare_names(self):
        """backends=[] + use_session：名字无前缀、server=default、raw_name==name（逐字同现状）"""
        session = _mock_session(
            [
                _make_tool("query_card_bill", "查账单", destructive=False),
                _make_tool("apply_bill_installment", "办分期", destructive=True),
            ]
        )
        client = MCPToolClient(MCPSettings(enabled=True))
        await client.use_session(session)

        names = {t.name for t in await client.list_tools()}
        assert names == {"query_card_bill", "apply_bill_installment"}
        spec = client.get_tool("query_card_bill")
        assert spec is not None
        assert spec.server == "default"
        assert spec.raw_name == "query_card_bill"
        assert client.is_sensitive("apply_bill_installment") is True

        await client.call_tool("query_card_bill", {"card_no": "6225880000001234"})
        assert session.call_tool.await_args.args[0] == "query_card_bill"


class TestMultiBackendE2E:
    """基于真实内存 round-trip 的多后端端到端验证（两个参考 server，不同前缀）"""

    async def test_two_reference_servers_union_and_dispatch(self):
        settings = MCPSettings(
            enabled=True,
            backends=[
                MCPBackend(name="alpha", endpoint="memory://alpha", prefix="a."),
                MCPBackend(name="beta", endpoint="memory://beta", prefix="b."),
            ],
        )
        server_a = build_reference_server(name="ref-alpha")
        server_b = build_reference_server(name="ref-beta")
        async with (
            connect_in_memory(server_a._mcp_server) as sess_a,
            connect_in_memory(server_b._mcp_server) as sess_b,
        ):
            await sess_a.initialize()
            await sess_b.initialize()
            client = MCPToolClient(settings)
            await client.use_backend_sessions({"alpha": sess_a, "beta": sess_b})

            names = {t.name for t in await client.list_tools()}
            assert {"a.query_card_bill", "a.query_points"} <= names
            assert {"b.query_card_bill", "b.query_points"} <= names

            # 分发到正确后端：raw_name 去前缀后真实 round-trip 成功（非 error）
            r = await client.call_tool("b.query_card_bill", {"card_no": "6225880000001234"})
            assert r["is_error"] is False
            assert "6225880000001234" in r["content"]
