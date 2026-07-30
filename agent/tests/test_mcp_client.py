"""MCP 工具客户端单元测试"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.common.mcp_client import MCPToolClient, ToolSpec
from smartcs.shared.config import MCPSettings


def _make_tool(name: str, description: str = "", *, destructive: bool | None = None, schema: dict | None = None):
    """构造类 mcp.types.Tool 的轻量对象"""
    annotations = SimpleNamespace(destructiveHint=destructive) if destructive is not None else None
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {}},
        annotations=annotations,
    )


class TestDisabled:
    """MCP_ENABLED=False 时的零回归行为"""

    async def test_connect_noop_when_disabled(self):
        client = MCPToolClient(settings=MCPSettings(enabled=False))
        await client.connect()
        assert client.connected is False
        assert await client.list_tools() == []
        assert client.to_openai_tools() == []
        await client.close()

    async def test_connect_failure_graceful(self):
        """endpoint 不可达时应降级为无工具，而非抛出"""
        from unittest.mock import patch

        client = MCPToolClient(settings=MCPSettings(enabled=True, endpoint="http://gw/mcp"))
        with patch(
            "smartcs.services.common.mcp_client.streamablehttp_client",
            side_effect=ConnectionError("connection refused"),
        ):
            await client.connect()
        assert client.connected is False
        assert await client.list_tools() == []


class TestRefreshTools:
    """工具目录拉取与敏感性判定"""

    async def test_sensitivity_from_annotation_and_config(self):
        client = MCPToolClient(settings=MCPSettings(enabled=True, sensitive_tools=["bill_installment"]))
        mock_session = MagicMock()
        mock_session.list_tools = AsyncMock(
            return_value=SimpleNamespace(
                tools=[
                    _make_tool("query_balance", "查询余额", destructive=False),
                    _make_tool("card_loss", "银行卡挂失", destructive=True),
                    _make_tool("bill_installment", "账单分期"),  # 无注解，靠配置命中
                ]
            )
        )
        client._session = mock_session
        client._connected = True
        await client._refresh_tools()

        tools = await client.list_tools()
        by_name = {t.name: t for t in tools}
        assert by_name["query_balance"].sensitive is False
        assert by_name["card_loss"].sensitive is True  # destructiveHint
        assert by_name["bill_installment"].sensitive is True  # 配置白名单
        assert client.is_sensitive("card_loss") is True
        assert client.is_sensitive("query_balance") is False

    async def test_to_openai_tools_format(self):
        client = MCPToolClient(settings=MCPSettings(enabled=True))
        client._tools_cache = [
            ToolSpec(name="query_balance", description="查询余额", input_schema={"type": "object", "properties": {}})
        ]
        openai_tools = client.to_openai_tools()
        assert openai_tools[0]["type"] == "function"
        assert openai_tools[0]["function"]["name"] == "query_balance"
        assert openai_tools[0]["function"]["description"] == "查询余额"

    async def test_get_tool(self):
        client = MCPToolClient(settings=MCPSettings(enabled=True))
        spec = ToolSpec(name="foo", description="bar")
        client._tools_cache = [spec]
        assert client.get_tool("foo") is spec
        assert client.get_tool("missing") is None


class TestCallTool:
    """工具执行"""

    async def test_call_tool_parses_text_content(self):
        client = MCPToolClient(settings=MCPSettings(enabled=True))
        client._connected = True
        mock_session = MagicMock()
        mock_session.call_tool = AsyncMock(
            return_value=SimpleNamespace(
                content=[SimpleNamespace(text="您的可用余额为 100 元")],
                structuredContent=None,
                isError=False,
            )
        )
        client._session = mock_session

        result = await client.call_tool("query_balance", {"card": "1234"})
        assert result["is_error"] is False
        assert "100 元" in result["content"]

    async def test_call_tool_structured_fallback(self):
        client = MCPToolClient(settings=MCPSettings(enabled=True))
        client._connected = True
        mock_session = MagicMock()
        mock_session.call_tool = AsyncMock(
            return_value=SimpleNamespace(content=[], structuredContent={"balance": 100}, isError=False)
        )
        client._session = mock_session
        result = await client.call_tool("query_balance", {})
        assert "balance" in result["content"]

    async def test_call_tool_raises_when_not_connected(self):
        client = MCPToolClient(settings=MCPSettings(enabled=True))
        with pytest.raises(RuntimeError, match="未连接"):
            await client.call_tool("query_balance", {})
