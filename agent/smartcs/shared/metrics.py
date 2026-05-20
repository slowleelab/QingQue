"""Prometheus 指标定义与 /metrics 端点

提供请求计数、请求耗时直方图等基础指标，
以及会话生命周期指标（转换次数、停留时长、超时触发率）。
两个 FastAPI 服务共用。
"""

from __future__ import annotations

import time

from prometheus_client import REGISTRY, Counter, Histogram, generate_latest
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
