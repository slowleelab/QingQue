"""可观测性单元测试：指标、追踪、日志↔链路关联

不依赖 live 中间件/服务器：用 ASGI 内存传输、进程内 TracerProvider 与 httpx MockTransport
自包含验证指标暴露、PrometheusMiddleware 计数、@traced/get_trace_context/TraceContextFilter
行为，以及 HTTPX 出站请求携带 W3C traceparent（Python→Java 传播的前提）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY, generate_latest

from lumio.shared import metrics as _metrics  # noqa: F401  确保指标注册进默认 REGISTRY
from lumio.shared import tracing


@pytest.fixture(autouse=True)
def _reset_otel_tracer_provider():
    """P1-6 修复: 还原 OTel 全局 _TRACER_PROVIDER + tracing._TRACING_ENABLED.

    之前几个 test 直接 `trace._TRACER_PROVIDER = provider` 注入 (绕过
    set_tracer_provider 的 Once 守卫). Once 已 set, 后续 test 触发
    OTel 初始化时拿不到 _TRACER_PROVIDER, 在 opentelemetry/trace/__init__.py:255
    形成自递归. 在 fixture teardown 完整还原: 删 module attrs + 重置 Once flag.
    """
    from opentelemetry.trace import ProxyTracerProvider

    # 保存初始状态
    original_provider = getattr(trace, "_TRACER_PROVIDER", None)
    original_proxy = getattr(trace, "_PROXY_TRACER_PROVIDER", ProxyTracerProvider())
    once_reset_token = None
    if hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
        once_reset_token = trace._TRACER_PROVIDER_SET_ONCE._done  # type: ignore[attr-defined]
    original_enabled = tracing._TRACING_ENABLED
    yield
    # 完整还原: 清掉 module 注入, 重置 Once 让 set_tracer_provider 可再次 set
    trace._TRACER_PROVIDER = None
    trace._PROXY_TRACER_PROVIDER = original_proxy
    if once_reset_token is not None and hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
        trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    tracing._TRACING_ENABLED = original_enabled
    # 恢复 _TRACER_PROVIDER 到原值或 None
    trace._TRACER_PROVIDER = original_provider

# ── 指标定义与 /metrics 暴露 ──


def test_metric_names_registered() -> None:
    """关键指标名出现在 /metrics 输出（Prometheus 文本格式）"""
    from prometheus_client import REGISTRY, generate_latest

    text = generate_latest(REGISTRY).decode()
    for name in (
        "http_requests_total",
        "http_request_duration_seconds",
        "llm_call_duration_seconds",
        "lumio_fast_reply_total",
        "lumio_agent_responses_total",
        "lumio_bot_semaphore_utilization",
        "lumio_active_workers",
        "lumio_stream_length",
        "lumio_stream_pending_total",
        "tool_calls_total",
    ):
        assert name in text, f"指标 {name} 未在 /metrics 暴露"


async def test_prometheus_middleware_counts() -> None:
    """PrometheusMiddleware 对普通请求计数，且排除 /metrics 自身"""
    from lumio.shared.metrics import REQUEST_COUNT, PrometheusMiddleware, metrics_endpoint

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    wrapped = PrometheusMiddleware(app)

    def _count() -> float:
        return REQUEST_COUNT.labels(method="GET", endpoint="/probe", status=200)._value.get()

    before = _count()
    transport = httpx.ASGITransport(app=wrapped)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/probe")
    assert resp.status_code == 200
    assert _count() == before + 1

    # metrics_endpoint 本身可返回文本
    from starlette.requests import Request

    scope = {"type": "http", "method": "GET", "path": "/metrics", "headers": []}
    req = Request(scope)
    out = await metrics_endpoint(req)
    assert out.status_code == 200


# ── @traced / get_trace_context ──


async def test_traced_passthrough_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """tracing 关闭时 @traced 为透明包装，不建 span"""
    monkeypatch.setattr(tracing, "_TRACING_ENABLED", False)

    @tracing.traced("noop")
    async def f(x: int) -> int:
        return x + 1

    assert await f(1) == 2
    assert tracing.get_trace_context() is None


async def test_traced_creates_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """tracing 开启时 @traced 建 span，get_trace_context 返回 (trace_id, span_id)"""
    monkeypatch.setattr(tracing, "_TRACING_ENABLED", True)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    seen: dict[str, tuple[str, str] | None] = {}

    @tracing.traced("Biz.op")
    async def f() -> None:
        seen["ctx"] = tracing.get_trace_context()

    tracer_provider_token = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider  # 直接注入，避免 set_tracer_provider 的一次性限制
    try:
        await f()
    finally:
        trace._TRACER_PROVIDER = tracer_provider_token

    spans = exporter.get_finished_spans()
    assert any(s.name == "Biz.op" for s in spans)
    assert seen["ctx"] is not None
    trace_id, span_id = seen["ctx"]
    assert len(trace_id) == 32 and len(span_id) == 16


# ── TraceContextFilter → JSONFormatter ──


def test_trace_filter_omits_without_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """无活跃 span 时日志不含 trace 字段"""
    import logging

    from lumio.shared.logger import JSONFormatter, TraceContextFilter

    monkeypatch.setattr(tracing, "_TRACING_ENABLED", False)
    rec = logging.LogRecord("t", logging.INFO, "x.py", 1, "hi", None, None)
    TraceContextFilter().filter(rec)
    out = JSONFormatter().format(rec)
    assert "trace_id" not in out


def test_trace_filter_injects_with_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """有活跃 span 时日志含 trace_id/span_id"""
    import json
    import logging

    from lumio.shared.logger import JSONFormatter, TraceContextFilter

    monkeypatch.setattr(tracing, "_TRACING_ENABLED", True)
    provider = TracerProvider()
    saved = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider
    try:
        tracer = trace.get_tracer("t")
        with tracer.start_as_current_span("s"):
            rec = logging.LogRecord("t", logging.INFO, "x.py", 1, "hi", None, None)
            TraceContextFilter().filter(rec)
            payload = json.loads(JSONFormatter().format(rec))
    finally:
        trace._TRACER_PROVIDER = saved

    assert len(payload["trace_id"]) == 32
    assert len(payload["span_id"]) == 16


# ── HTTPX 出站 traceparent 注入（Python→Java 传播） ──


def test_httpx_injects_traceparent() -> None:
    """活跃 span 内的 httpx 出站请求自动携带 W3C traceparent 头"""
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    saved = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider

    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["traceparent"] = request.headers.get("traceparent")
        return httpx.Response(200, text="ok")

    HTTPXClientInstrumentor().instrument()
    try:
        tracer = trace.get_tracer("t")
        client = httpx.Client(transport=httpx.MockTransport(handler))
        # 显式包裹本 client 的（Mock）transport——全局 instrument 只 patch 默认 transport 类
        HTTPXClientInstrumentor().instrument_client(client, tracer_provider=provider)
        with tracer.start_as_current_span("client"), client:
            client.get("http://downstream/mcp")
    finally:
        HTTPXClientInstrumentor().uninstrument()
        trace._TRACER_PROVIDER = saved

    assert captured["traceparent"] is not None
    assert captured["traceparent"].startswith("00-")


# ── MCP.call_tool span 增强 (server/duration/is_error) ──


async def test_mcp_call_tool_span_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """call_tool span 携带 mcp.tool / mcp.server / mcp.duration_ms / mcp.is_error"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from lumio.services.common import mcp_client

    # 1) 打开 tracing + 注入 InMemorySpanExporter
    monkeypatch.setattr(mcp_client, "_TRACING_ENABLED", True)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    saved = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider

    # 2) 构造一个最小 MCPClient: 绕过 connect/list, 只跑 call_tool
    #    _dispatch 命中表示走多后端, 让 mcp.server 属性有值
    settings = mcp_client.MCPSettings()
    client = mcp_client.MCPToolClient(settings)
    client._connected = True
    client._dispatch = {"query_demo": ("credit-card", "query_demo")}
    client._session_by_server = {
        "credit-card": MagicMock(call_tool=AsyncMock()),
    }
    # mock session.call_tool 返回 isError=False 的内容
    fake_block = SimpleNamespace(text="ok")
    client._session_by_server["credit-card"].call_tool.return_value = SimpleNamespace(
        content=[fake_block],
        structuredContent=None,
        isError=False,
    )

    try:
        result = await client.call_tool("query_demo", {"foo": "bar"})
    finally:
        trace._TRACER_PROVIDER = saved

    # 业务返回不受影响
    assert result == {"is_error": False, "content": "ok", "structured": None}

    # 3) 校验 span 属性
    spans = exporter.get_finished_spans()
    mcp_spans = [s for s in spans if s.name == "MCP.call_tool"]
    assert len(mcp_spans) == 1, f"应产生 1 个 MCP.call_tool span, 实际 {len(mcp_spans)}"
    attrs = dict(mcp_spans[0].attributes or {})
    assert attrs.get("mcp.tool") == "query_demo"
    assert attrs.get("mcp.server") == "credit-card"
    assert attrs.get("mcp.is_error") is False
    assert isinstance(attrs.get("mcp.duration_ms"), int)
    assert attrs["mcp.duration_ms"] >= 0


async def test_mcp_call_tool_error_span_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """call_tool 异常时 span 记录 exception + set_status=ERROR + mcp.duration_ms 仍记"""
    from unittest.mock import AsyncMock, MagicMock

    from lumio.services.common import mcp_client

    monkeypatch.setattr(mcp_client, "_TRACING_ENABLED", True)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    saved = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider

    settings = mcp_client.MCPSettings()
    client = mcp_client.MCPToolClient(settings)
    client._connected = True
    client._session = MagicMock(call_tool=AsyncMock(side_effect=RuntimeError("boom")))

    try:
        with pytest.raises(RuntimeError, match="boom"):
            await client.call_tool("query_demo", {})
    finally:
        trace._TRACER_PROVIDER = saved

    spans = exporter.get_finished_spans()
    mcp_spans = [s for s in spans if s.name == "MCP.call_tool"]
    assert len(mcp_spans) == 1
    # 异常路径: status.code 应为 ERROR (值=2)
    from opentelemetry.trace import StatusCode

    assert mcp_spans[0].status.status_code == StatusCode.ERROR
    # 记录了异常事件
    events = mcp_spans[0].events or []
    assert any(e.name == "exception" for e in events)


# ── 看板↔代码指标一致性（防漂移） ──


def _collect_dashboard_metric_refs(dash_path: Path) -> set[str]:
    """提取 dashboard JSON 中 panel.targets.expr 引用的指标名 (基名, 去 _bucket/_sum/_count).

    兼容两种 Grafana export 格式:
    - 新: 顶层 {panels: [...]} (如 lumio-overview.json / middleware.json)
    - 旧: 顶层 {dashboard: {panels: [...]}} (如 lumio-dashboard.json, 旧版 export)
    """
    owned = re.compile(
        r"\b(lumio_[a-z_]+|llm_call_duration_seconds|http_request[a-z_]*"
        r"|http_requests_total|session_[a-z_]+|tool_calls_total|mcp_tool_call[a-z_]+|mcp_tool_calls_total)"
    )
    text = dash_path.read_text(encoding="utf-8")
    data = json.loads(text)
    # 旧 schema: {dashboard: {...}}; 新 schema: {...} 直接含 panels
    panels = data.get("panels") or data.get("dashboard", {}).get("panels", [])
    exprs: list[str] = []
    for panel in panels:
        for tgt in panel.get("targets", []):
            if "expr" in tgt:
                exprs.append(tgt["expr"])
    referenced: set[str] = set()
    for expr in exprs:
        for m in owned.findall(expr):
            base = re.sub(r"_(bucket|sum|count)$", "", m)
            referenced.add(base)
    return referenced


@pytest.mark.parametrize(
    "dash_name",
    [
        "lumio-overview.json",  # 主面板, Python + Java 混合, 0 缺口
        "middleware.json",  # 中间件面板, 全是 exporter 指标, 0 业务指标缺口
        "lumio-dashboard.json",  # 旧 export 格式, commit 6a 修 5 处 + 删 1 panel, 6b 补 2 个新指标
    ],
)
def test_dashboard_metric_names_defined(dash_name: str) -> None:
    """Grafana 看板引用的 lumio_*/llm_/http_/session_/tool_ 指标均在代码中定义.

    Java mcp_* 不在 Python REGISTRY, 由 test_mcp_server_metrics_in_prometheus_path
    单独校验静态路径.
    """
    dash = Path(__file__).resolve().parents[2] / "config" / "grafana" / "dashboards" / dash_name
    referenced = _collect_dashboard_metric_refs(dash)
    metrics_text = generate_latest(REGISTRY).decode()

    missing = [name for name in referenced if name not in metrics_text and not name.startswith("mcp_")]
    assert not missing, f"{dash_name} 引用了 Python REGISTRY 缺失的指标: {missing}"


def test_mcp_server_metrics_in_prometheus_path() -> None:
    """Java mcp-server 切面产出的 mcp_tool_calls_total / mcp_tool_call_duration 指标,
    与 config/prometheus.yml scrape job 路径一致 (静态解析, 不连真实服务)

    防止两类漂移:
    1. Java 切面改了 metric 名, prometheus.yml scrape 仍按旧名 → 抓不到
    2. prometheus.yml scrape job target 改了, mcp-server 启动后无法被发现
    3. dashboard 引用 Timer 未带 _seconds 后缀 (Micrometer 命名约定)
    """
    repo_root = Path(__file__).resolve().parents[2]
    prom_yml = repo_root / "config" / "prometheus.yml"
    aspect_java = (
        repo_root
        / "mcp-server"
        / "src"
        / "main"
        / "java"
        / "com"
        / "lumio"
        / "mcp"
        / "observability"
        / "ToolCallAspect.java"
    )

    prom_text = prom_yml.read_text(encoding="utf-8")
    aspect_text = aspect_java.read_text(encoding="utf-8")

    # 1) prometheus.yml: mcp-server job 用 /actuator/prometheus 路径
    assert 'job_name: "mcp-server"' in prom_text
    assert "metrics_path: /actuator/prometheus" in prom_text
    assert "host.docker.internal:8090" in prom_text

    # 2) Java 切面定义的两个 metric 名
    assert "mcp_tool_calls_total" in aspect_text
    assert "mcp_tool_call_duration" in aspect_text

    # 3) Micrometer 命名约定: Timer 暴露为 *_seconds_bucket/_sum/_count,
    #    Counter 暴露为同名. dashboard 引用 Timer 必须带 _seconds 后缀
    dash_overview = repo_root / "config" / "grafana" / "dashboards" / "lumio-overview.json"
    dash_data = json.loads(dash_overview.read_text(encoding="utf-8"))
    panels = dash_data.get("panels") or dash_data.get("dashboard", {}).get("panels", [])
    exprs: list[str] = []
    for panel in panels:
        for tgt in panel.get("targets", []):
            if "expr" in tgt:
                exprs.append(tgt["expr"])
    mcp_timer_refs = [e for e in exprs if "mcp_tool_call_duration" in e]
    # Timer 引用必须带 _seconds 后缀 (Micrometer 命名约定)
    for expr in mcp_timer_refs:
        assert (
            "mcp_tool_call_duration_seconds" in expr
        ), f"dashboard 引用 mcp_tool_call_duration 未带 _seconds 后缀: {expr}"


# ── Commit 8: Sampling 抽到 Settings ──


def test_observability_settings_sampling_ratio_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """ObservabilitySettings.sampling_ratio 默认 1.0, 显式 0.0/1.0 合法."""
    from lumio.shared.config import ObservabilitySettings

    cfg = ObservabilitySettings()
    assert cfg.sampling_ratio == 1.0
    # 边界值: 0.0 (全丢) 和 1.0 (全采) 都应合法 (ge=0, le=1).
    # 因字段有 AliasChoices, 用 env 注入验证.
    monkeypatch.setenv("OBSERVABILITY_SAMPLING_RATIO", "0.0")
    cfg_lo = ObservabilitySettings()
    assert cfg_lo.sampling_ratio == 0.0
    monkeypatch.setenv("OBSERVABILITY_SAMPLING_RATIO", "1.0")
    cfg_hi = ObservabilitySettings()
    assert cfg_hi.sampling_ratio == 1.0
    # LUMIO_TRACING_SAMPLE 别名 (Java compose 习惯) 也应驱动
    monkeypatch.setenv("OBSERVABILITY_SAMPLING_RATIO", "0.5")
    monkeypatch.setenv("LUMIO_TRACING_SAMPLE", "0.3")
    cfg_alias = ObservabilitySettings()
    # AliasChoices 顺序: 第一个命中即返回; OBSERVABILITY_ 优先 → 0.5
    assert cfg_alias.sampling_ratio == 0.5


def test_observability_settings_sampling_ratio_field_metadata() -> None:
    """sampling_ratio 字段同时支持 OBSERVABILITY_SAMPLING_RATIO 与 LUMIO_TRACING_SAMPLE.

    通过 Pydantic 模型构造覆盖两种别名, 验证 AliasChoices 注册成功 (零回归承诺).
    """
    from pydantic import AliasChoices

    from lumio.shared.config import ObservabilitySettings

    field = ObservabilitySettings.model_fields["sampling_ratio"]
    choices: AliasChoices = field.validation_alias  # type: ignore[assignment]
    # AliasChoices 是 Pydantic 特定类, 暴露 choices 属性
    aliases = list(choices.choices)
    assert "OBSERVABILITY_SAMPLING_RATIO" in aliases
    assert "LUMIO_TRACING_SAMPLE" in aliases
