"""Prometheus 指标中间件

暴露 /metrics 端点，采集 HTTP 请求量/延迟/错误率。
prometheus_client 自动采集 Python 进程/GC 指标。
"""

from __future__ import annotations

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from kb.logging import get_logger

logger = get_logger(__name__)

# ── 指标定义 ──

REQUEST_COUNT = Counter(
    "kb_http_requests_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "kb_http_request_duration_seconds",
    "HTTP 请求延迟（秒）",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

INGEST_COUNT = Counter(
    "kb_ingest_total",
    "文档摄入总数",
    ["status"],
)

RETRIEVE_COUNT = Counter(
    "kb_retrieve_total",
    "检索请求总数",
    ["search_type", "status"],
)

RETRIEVE_LATENCY = Histogram(
    "kb_retrieve_duration_seconds",
    "检索延迟（秒，含降级路径）",
    ["search_type"],
    # I2-C2: 扩 bucket 覆盖到 P99.9 (1.5/2.0/2.5/5.0/10.0)
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 5.0, 10.0),
)

# I2-C2: 检索降级事件计数 (from_stage -> to_stage)
RETRIEVE_DEGRADATION = Counter(
    "kb_retrieve_degradation_total",
    "检索降级事件 (from -> to, 例如 hybrid->bm25)",
    ["from", "to"],
)

# I2-C2: 熔断器状态 — Gauge, 由 health-check 端点暴露
CIRCUIT_BREAKER_STATE = Gauge(
    "kb_circuit_breaker_state",
    "熔断器状态 (0=closed, 1=half_open, 2=open)",
    ["name"],
)

# I2-C1: 限流超限计数 (tier × path_group)
RATE_LIMIT_EXCEEDED = Counter(
    "kb_rate_limit_exceeded_total",
    "限流超限次数 (tier + path_group)",
    ["tier", "path_group"],
)

# I2-C4: 嵌入漂移监控
EMBEDDING_DRIFT_SCORE = Histogram(
    "kb_embedding_drift_score",
    "嵌入漂移 score (cosine distance to centroid, 越大越漂移)",
    buckets=(0.01, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0),
)

EMBEDDING_DRIFT_DETECTED = Counter(
    "kb_embedding_drift_detected_total",
    "嵌入漂移告警触发次数 (超过阈值)",
    ["severity"],  # info (>=0.1) / warn (>=0.15) / critical (>=0.3)
)

# I2-C4: 影子索引对比一致性
SHADOW_DIVERGENCE = Histogram(
    "kb_shadow_divergence",
    "影子 vs 主索引 Jaccard 距离 (1 - similarity, 越大越不一致)",
    buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0),
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Prometheus 指标采集中间件"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # /metrics 端点本身不采集
        if request.url.path == "/metrics":
            return await call_next(request)

        start = time.perf_counter()

        try:
            response = await call_next(request)
            latency = time.perf_counter() - start

            # 归一化路径（去掉路径参数）
            path = _normalize_path(request.url.path)
            REQUEST_COUNT.labels(
                method=request.method,
                path=path,
                status=str(response.status_code),
            ).inc()
            REQUEST_LATENCY.labels(method=request.method, path=path).observe(latency)

            return response

        except Exception:
            latency = time.perf_counter() - start
            path = _normalize_path(request.url.path)
            REQUEST_COUNT.labels(
                method=request.method,
                path=path,
                status="500",
            ).inc()
            REQUEST_LATENCY.labels(method=request.method, path=path).observe(latency)
            raise


def metrics_endpoint():
    """Prometheus /metrics 端点"""
    return PlainTextResponse(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


def _normalize_path(path: str) -> str:
    """归一化路径，将 UUID/数字替换为 :param"""
    import re

    # 替换 UUID
    path = re.sub(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "/:id", path)
    # 替换纯数字
    path = re.sub(r"/\d+", "/:id", path)
    return path
