"""Prometheus 指标定义与 /metrics 端点

提供请求计数、请求耗时直方图等基础指标，
以及会话生命周期指标（转换次数、停留时长、超时触发率）。
两个 FastAPI 服务共用。
"""

from __future__ import annotations

import time

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

# ── HTTP 指标 ──

REQUEST_COUNT = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ── 会话生命周期指标 ──

SESSION_TRANSITIONS = Counter(
    "session_transitions_total",
    "会话状态转换次数",
    ["from_phase", "from_sub", "to_phase", "to_sub", "reason"],
)

SESSION_TIMEOUTS = Counter(
    "session_timeouts_total",
    "会话超时触发次数",
    ["sub_phase", "reason"],
)

SESSION_PHASE_DURATION = Histogram(
    "session_phase_duration_seconds",
    "会话各子阶段停留时长（秒）",
    ["sub_phase"],
    buckets=[5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600],
)

# ── 工具调用指标 ──

TOOL_CALLS = Counter(
    "tool_calls_total",
    "MCP 工具调用次数",
    ["tool", "status"],  # status: success/error
)

TOOL_CONFIRMATIONS = Counter(
    "tool_confirmations_total",
    "敏感工具确认决策次数",
    ["decision"],  # decision: pending/confirm/cancel/unclear/expired
)

TOOL_GUARD_DENIALS = Counter(
    "tool_guard_denials_total",
    "被工具护栏拦截（授权/额度）的工具调用次数",
    ["tool", "reason"],  # reason: role_denied/amount_exceeded
)

# ── LLM 调用指标 ──

LLM_CALL_DURATION = Histogram(
    "llm_call_duration_seconds",
    "LLM 调用耗时（秒）",
    ["model", "method"],  # method: chat/chat_with_tools
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0],
)

# ── Bot 运行时指标（由 router 监控循环周期性刷新） ──

BOT_FAST_REPLY = Counter(
    "lumio_fast_reply_total",
    "Bot 快速兜底回复次数（并发满载时的模板回复）",
)

BOT_AGENT_RESPONSES = Counter(
    "lumio_agent_responses_total",
    "Bot Agent 正常回复次数",
    ["source"],  # source: llm/template/fallback/tool_* 等
)

BOT_SEMAPHORE_UTILIZATION = Gauge(
    "lumio_bot_semaphore_utilization",
    "Bot Agent 信号量利用率（0~1，已占用槽位 / 总槽位）",
)

BOT_ACTIVE_WORKERS = Gauge(
    "lumio_active_workers",
    "Bot 当前活跃的会话 worker 数",
)

BOT_STREAM_LENGTH = Gauge(
    "lumio_stream_length",
    "Bot 聊天消息流（Redis Stream）长度",
)

BOT_STREAM_PENDING = Gauge(
    "lumio_stream_pending_total",
    "Bot 聊天消息流待确认（PEL pending）消息数",
)

# ── RAG 检索 + 降级状态指标（commit 6b 补 dashboard 缺口） ──

RETRIEVE_DURATION = Histogram(
    "lumio_retrieval_duration_seconds",
    "RAG 检索端到端耗时（秒），含 BM25/向量/混合各路径",
    ["search_type"],  # search_type: hybrid/bm25/vector
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0],
)

DEGRADATION_LEVEL = Gauge(
    "lumio_degradation_level",
    "系统降级等级（0=normal, 1=degraded, 2=fallback），由 DegradationManager 写入",
)

# 排除自采集，避免 Prometheus 抓取 /metrics 产生反馈循环
_EXCLUDED_PATHS = {"/metrics", "/health", "/favicon.ico"}


async def metrics_endpoint(request: Request) -> Response:
    """暴露 /metrics 供 Prometheus 采集"""
    output = generate_latest(REGISTRY)
    return Response(content=output, media_type="text/plain; version=0.0.4; charset=utf-8")


class PrometheusMiddleware:
    """Starlette 中间件，采集每个请求的计数和耗时"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # 排除指标端点自身，避免反馈循环
        if path in _EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        start = time.perf_counter()
        status_code = 200

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            REQUEST_COUNT.labels(method=method, endpoint=path, status=status_code).inc()
            REQUEST_LATENCY.labels(method=method, endpoint=path).observe(duration)
