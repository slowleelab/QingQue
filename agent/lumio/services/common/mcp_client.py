"""MCP 工具客户端

经 Higress AI 网关调用受治理的工具（新增工具经 Spring AI Alibaba MCP Server 暴露，
存量 REST 接口经 Higress REST-to-MCP 转换）。本模块只负责「调用」，
身份注入 / 鉴权 / 脱敏 / 限流 / 审计等治理均由 Higress 侧统一承担。

设计红线：``MCP_ENABLED=False`` 或网关不可达时，``list_tools()`` 返回 ``[]``，
编排层据此回落原有 RAG/LLM 生成路径，实现零回归。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Collection
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from lumio.shared.config import MCPBackend, MCPSettings, get_settings
from lumio.shared.tracing import _TRACING_ENABLED

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """工具规格（从 MCP server 拉取的 schema + 敏感性判定）"""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    # 敏感标记：来自工具注解 destructiveHint，与配置 sensitive_tools 取并集
    sensitive: bool = False
    # 来源后端逻辑名（路由模式）；单后端默认 "default"
    server: str = "default"
    # 后端侧原始工具名（去掉域前缀）；单后端 == name
    raw_name: str = ""

    def __post_init__(self) -> None:
        # raw_name 缺省时回填为 name → 单后端行为不变（零回归）
        if not self.raw_name:
            self.raw_name = self.name

    def to_openai_tool(self) -> dict[str, Any]:
        """转换为 OpenAI function-calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


@dataclass
class _BoundBackend:
    """已绑定的后端会话（内部）"""

    name: str
    session: ClientSession
    prefix: str = ""
    sensitive: set[str] = field(default_factory=set)


class MCPToolClient:
    """MCP 工具客户端

    单后端（默认）持有一条到 Higress MCP 入口的长连接（streamable-http），
    在应用 lifespan 期间保持，缓存工具 schema。

    路由模式（``MCPSettings.backends`` 非空）下持有多条后端连接，合并各后端工具目录，
    按域前缀生成 host-facing 工具名并建立 ``name→(server, raw_name)`` 分发索引；
    某后端连接/列举失败仅使其工具缺席，不影响其余后端与主链路（优雅降级）。
    """

    def __init__(self, settings: MCPSettings | None = None) -> None:
        self._settings = settings or get_settings().mcp
        # 单后端兼容路径：直接绑定的会话（use_session / 旧测试直接赋值）
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        # 路由模式：已绑定的多后端
        self._backends: list[_BoundBackend] = []
        self._tools_cache: list[ToolSpec] = []
        # host-facing 工具名 → (server, raw_name) 分发索引
        self._dispatch: dict[str, tuple[str, str]] = {}
        # server 逻辑名 → 会话
        self._session_by_server: dict[str, ClientSession] = {}
        self._connected = False

    @property
    def connected(self) -> bool:
        """是否已成功连接（enabled 且握手成功）"""
        return self._connected

    async def connect(self) -> None:
        """建立到 MCP 后端的连接并拉取工具目录

        单后端（``backends`` 为空）：连一条 ``endpoint``。
        路由模式（``backends`` 非空）：逐个连接，失败的后端跳过（其工具缺席）。

        任何异常均被吞掉（记录日志）：连接失败视为「无可用工具」，
        由编排层回落，不影响主链路。
        """
        if not self._settings.enabled:
            logger.info("MCP 工具层已禁用（MCP_ENABLED=False），跳过连接")
            return

        backends_cfg = self._settings.backends or [
            MCPBackend(name="default", endpoint=self._settings.endpoint, prefix="")
        ]

        stack = AsyncExitStack()
        bound: list[_BoundBackend] = []
        for cfg in backends_cfg:
            try:
                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(
                        url=cfg.endpoint,
                        timeout=timedelta(seconds=self._settings.timeout_seconds),
                    )
                )
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                bound.append(
                    _BoundBackend(
                        name=cfg.name,
                        session=session,
                        prefix=cfg.prefix,
                        sensitive=set(cfg.sensitive_tools),
                    )
                )
                logger.info("MCP 后端已连接: name=%s endpoint=%s", cfg.name, cfg.endpoint)
            except Exception as exc:
                logger.warning("MCP 后端 %s 连接失败，跳过（其工具缺席）: %s", cfg.name, exc)
                continue

        if not bound:
            logger.warning("MCP 所有后端连接失败，将以无工具模式运行")
            await stack.aclose()
            self._reset_state()
            return

        self._backends = bound
        self._exit_stack = stack
        # 单后端时保留 self._session 兼容旧路径
        if len(bound) == 1:
            self._session = bound[0].session
        self._connected = True
        await self._refresh_tools()
        logger.info("MCP 已连接: backends=%d, tools=%d", len(bound), len(self._tools_cache))

    async def use_session(self, session: ClientSession) -> None:
        """绑定一条外部已建立并已 ``initialize()`` 的 ClientSession（单后端）

        用于将「参考 MCP Server」以进程内内存传输接入，或在集成测试中复用
        真实 ClientSession，从而在不依赖 Higress / 网络的前提下端到端跑通工具链。
        生产路径仍走 :meth:`connect`（streamable-http）；本入口不接管会话生命周期，
        由调用方负责关闭。

        Args:
            session: 已完成握手（``initialize``）的 MCP 客户端会话
        """
        self._session = session
        self._backends = []
        self._connected = True
        await self._refresh_tools()
        logger.info("MCP 已绑定外部会话: tools=%d", len(self._tools_cache))

    async def use_backend_sessions(self, sessions: dict[str, ClientSession]) -> None:
        """绑定多个已建立的外部会话（server_name → session，路由模式）

        前缀与敏感集取自 ``settings.backends``（按 name 匹配），用于多后端集成测试；
        不接管会话生命周期，由调用方负责关闭。
        """
        prefix_by_name = {b.name: b.prefix for b in self._settings.backends}
        sensitive_by_name = {b.name: set(b.sensitive_tools) for b in self._settings.backends}
        self._session = None
        self._backends = [
            _BoundBackend(
                name=name,
                session=session,
                prefix=prefix_by_name.get(name, ""),
                sensitive=sensitive_by_name.get(name, set()),
            )
            for name, session in sessions.items()
        ]
        self._connected = True
        await self._refresh_tools()
        logger.info("MCP 已绑定多后端会话: backends=%d tools=%d", len(self._backends), len(self._tools_cache))

    def _reset_state(self) -> None:
        self._session = None
        self._exit_stack = None
        self._backends = []
        self._tools_cache = []
        self._dispatch = {}
        self._session_by_server = {}
        self._connected = False

    async def close(self) -> None:
        """关闭连接"""
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as exc:  # pragma: no cover - 关闭异常仅记录
                logger.warning("MCP 关闭连接异常: %s", exc)
        self._reset_state()

    def _resolve_backends(self) -> list[_BoundBackend]:
        """产出参与工具合并/分发的后端列表

        路由模式优先；否则回退单一 default 会话（含旧测试直接赋值 ``self._session``）。
        """
        if self._backends:
            return self._backends
        if self._session is not None:
            return [_BoundBackend(name="default", session=self._session, prefix="", sensitive=set())]
        return []

    async def _refresh_tools(self) -> None:
        """拉取各后端工具 schema，按前缀合并并缓存，判定敏感性、建分发索引

        某后端 ``list_tools`` 失败仅跳过其工具（优雅降级），不影响其余后端。
        """
        global_sensitive = set(self._settings.sensitive_tools)
        specs: list[ToolSpec] = []
        dispatch: dict[str, tuple[str, str]] = {}
        session_by_server: dict[str, ClientSession] = {}

        for backend in self._resolve_backends():
            session_by_server[backend.name] = backend.session
            try:
                result = await backend.session.list_tools()
            except Exception as exc:
                logger.warning("MCP 后端 %s 列举工具失败，跳过（其工具缺席）: %s", backend.name, exc)
                continue
            for tool in result.tools:
                raw_name = tool.name
                host_name = f"{backend.prefix}{raw_name}"
                annotations = tool.annotations
                # destructiveHint=True（破坏性/写操作）视为敏感；与后端/全局白名单取并集
                annotated_sensitive = bool(annotations and annotations.destructiveHint)
                sensitive = (
                    annotated_sensitive
                    or raw_name in backend.sensitive
                    or host_name in global_sensitive
                    or raw_name in global_sensitive
                )
                specs.append(
                    ToolSpec(
                        name=host_name,
                        description=tool.description or "",
                        input_schema=dict(tool.inputSchema) if tool.inputSchema else {},
                        sensitive=sensitive,
                        server=backend.name,
                        raw_name=raw_name,
                    )
                )
                dispatch[host_name] = (backend.name, raw_name)

        self._tools_cache = specs
        self._dispatch = dispatch
        self._session_by_server = session_by_server

    async def list_tools(self) -> list[ToolSpec]:
        """返回缓存的工具列表；未连接/禁用时返回 []（优雅降级）"""
        if not self._connected:
            return []
        return list(self._tools_cache)

    def to_openai_tools(self, names: Collection[str] | None = None) -> list[dict[str, Any]]:
        """缓存工具 → OpenAI tools 格式

        Args:
            names: 可选的工具名白名单，用于**渐进式暴露**（按意图/业务域只把相关工具喂给 LLM）。
                为 ``None`` 时返回全部缓存工具（默认，行为与现状完全一致，零回归）；
                给定时仅返回名称命中的工具，并保持缓存中的原有顺序。

        说明：本方法只提供「按名单过滤」的**机制**，不承载「哪个意图给哪些工具」的**策略**——
        策略应由掌握意图上下文的编排层（如 ``ToolCallingExecutor`` / ``bot_agent``）决定后传入。
        """
        if names is None:
            return [spec.to_openai_tool() for spec in self._tools_cache]
        allow = set(names)
        return [spec.to_openai_tool() for spec in self._tools_cache if spec.name in allow]

    def is_sensitive(self, tool_name: str) -> bool:
        """判定工具是否敏感（需用户确认）"""
        for spec in self._tools_cache:
            if spec.name == tool_name:
                return spec.sensitive
        # 未知工具：保守起见按配置白名单判定
        return tool_name in set(self._settings.sensitive_tools)

    def get_tool(self, tool_name: str) -> ToolSpec | None:
        """按名称查询工具规格，未命中返回 None"""
        for spec in self._tools_cache:
            if spec.name == tool_name:
                return spec
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行工具调用（路由模式下按分发索引派发到对应后端）

        Args:
            name: host-facing 工具名（多后端时含域前缀）。

        Returns:
            {"is_error": bool, "content": str, "structured": dict | None}
            内容已由 Higress 侧治理（脱敏/鉴权），Python 侧仍会在编排层二次脱敏。

        Raises:
            RuntimeError: 未连接时调用
        """
        if not self._connected:
            raise RuntimeError("MCP 未连接，无法调用工具")

        target = self._dispatch.get(name)
        if target is not None:
            server, raw_name = target
            session = self._session_by_server.get(server) or self._session
        else:
            # 分发索引未命中（如旧测试直接绑定 self._session 未刷新目录）：按原名走默认会话
            session, raw_name = self._session, name

        if session is None:
            raise RuntimeError("MCP 未连接，无法调用工具")

        # 手动 span 控制: 注解 mcp.server / mcp.duration_ms / mcp.is_error / error
        # 让 Jaeger 火焰图能按 server 排序、按 duration 找慢调用、按 is_error 标红
        # 注: 不再用 @traced 装饰器, 避免与本手动 span 嵌套 (产生双层 MCP.call_tool)
        start = time.monotonic()
        span_ctx: Any = None
        if _TRACING_ENABLED:
            try:
                from opentelemetry import trace as _otel_trace
                from opentelemetry.trace import Status, StatusCode

                tracer = _otel_trace.get_tracer("lumio")
                span_ctx = tracer.start_as_current_span("MCP.call_tool")
                span_ctx.__enter__()
                span = _otel_trace.get_current_span()
                span.set_attribute("mcp.tool", name)
                if target is not None:
                    span.set_attribute("mcp.server", target[0])
            except Exception:
                span_ctx = None
        try:
            result = await session.call_tool(
                raw_name,
                arguments,
                read_timeout_seconds=timedelta(seconds=self._settings.timeout_seconds),
            )
            text_parts: list[str] = []
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    text_parts.append(text)
            structured = result.structuredContent
            content = "\n".join(text_parts)
            if not content and structured is not None:
                content = json.dumps(structured, ensure_ascii=False)
            is_error = bool(result.isError)
            if span_ctx is not None:
                span = _otel_trace.get_current_span()
                span.set_attribute("mcp.is_error", is_error)
                span.set_attribute("mcp.duration_ms", int((time.monotonic() - start) * 1000))
            return {
                "is_error": is_error,
                "content": content,
                "structured": structured,
            }
        except Exception as err:
            if span_ctx is not None:
                try:
                    span = _otel_trace.get_current_span()
                    span.record_exception(err)
                    span.set_status(Status(StatusCode.ERROR))
                    span.set_attribute("mcp.duration_ms", int((time.monotonic() - start) * 1000))
                except Exception:
                    pass
            raise
        finally:
            if span_ctx is not None:
                with suppress(Exception):
                    span_ctx.__exit__(None, None, None)
