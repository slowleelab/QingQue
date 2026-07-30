"""可观测性单元测试：指标、追踪、日志↔链路关联

不依赖 live 中间件/服务器：用 ASGI 内存传输、进程内 TracerProvider 与 httpx MockTransport
自包含验证指标暴露、PrometheusMiddleware 计数、@traced/get_trace_context/TraceContextFilter
行为，以及 HTTPX 出站请求携带 W3C traceparent（Python→Java 传播的前提）。
"""

from __future__ import annotations

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from smartcs.shared import metrics as _metrics  # noqa: F401  确保指标注册进默认 REGISTRY
from smartcs.shared import tracing

# ── 指标定义与 /metrics 暴露 ──


def test_metric_names_registered() -> None:
    """关键指标名出现在 /metrics 输出（Prometheus 文本格式）"""
    from prometheus_client import REGISTRY, generate_latest

    text = generate_latest(REGISTRY).decode()
    for name in (
        "http_requests_total",
        "http_request_duration_seconds",
        "llm_call_duration_seconds",
        "smartcs_fast_reply_total",
        "smartcs_agent_responses_total",
        "smartcs_bot_semaphore_utilization",
        "smartcs_active_workers",
        "smartcs_stream_length",
        "smartcs_stream_pending_total",
        "tool_calls_total",
    ):
        assert name in text, f"指标 {name} 未在 /metrics 暴露"


async def test_prometheus_middleware_counts() -> None:
    """PrometheusMiddleware 对普通请求计数，且排除 /metrics 自身"""
    from smartcs.shared.metrics import REQUEST_COUNT, PrometheusMiddleware, metrics_endpoint

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

    from smartcs.shared.logger import JSONFormatter, TraceContextFilter

    monkeypatch.setattr(tracing, "_TRACING_ENABLED", False)
    rec = logging.LogRecord("t", logging.INFO, "x.py", 1, "hi", None, None)
    TraceContextFilter().filter(rec)
    out = JSONFormatter().format(rec)
    assert "trace_id" not in out


def test_trace_filter_injects_with_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """有活跃 span 时日志含 trace_id/span_id"""
    import json
    import logging

    from smartcs.shared.logger import JSONFormatter, TraceContextFilter

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


# ── 看板↔代码指标一致性（防漂移） ──


def test_dashboard_metric_names_defined() -> None:
    """Grafana 看板引用的 smartcs_*/llm_/http_/session_/tool_ 指标均在代码中定义"""
    import json
    import re
    from pathlib import Path

    from prometheus_client import REGISTRY, generate_latest

    dash = Path(__file__).resolve().parents[2] / "config" / "grafana" / "dashboards" / "smartcs-overview.json"
    text = dash.read_text(encoding="utf-8")
    data = json.loads(text)

    exprs: list[str] = []
    for panel in data.get("panels", []):
        for tgt in panel.get("targets", []):
            if "expr" in tgt:
                exprs.append(tgt["expr"])

    metrics_text = generate_latest(REGISTRY).decode()
    # 本仓库自有指标前缀（排除 Java mcp_* 与 Prometheus 函数名）
    owned = re.compile(r"\b(smartcs_[a-z_]+|llm_call_duration_seconds|http_request[a-z_]*|http_requests_total|session_[a-z_]+|tool_calls_total)")
    referenced: set[str] = set()
    for expr in exprs:
        for m in owned.findall(expr):
            # 去掉 _bucket/_sum/_count/_total 便于匹配基名
            base = re.sub(r"_(bucket|sum|count)$", "", m)
            referenced.add(base)

    missing = [name for name in referenced if name not in metrics_text]
    assert not missing, f"看板引用了未定义的指标: {missing}"
