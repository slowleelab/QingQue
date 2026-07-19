"""API 审计日志中间件

记录所有 API 请求的操作审计，满足银行合规要求。
append-only，包含：时间/操作者/方法/路径/状态码/IP/耗时。
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.logging import bind_context, get_logger

logger = get_logger(__name__)


class AuditMiddleware(BaseHTTPMiddleware):
    """审计日志中间件

    每个请求生成唯一 request_id，记录方法/路径/状态码/耗时/IP。
    通过 structlog contextvars 自动携带 request_id 到所有日志。
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 生成 request_id
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:12]

        # 绑定上下文
        bind_context(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            client_ip=request.client.host if request.client else "unknown",
        )

        start = time.perf_counter()

        try:
            response = await call_next(request)
            latency_ms = int((time.perf_counter() - start) * 1000)

            # 审计日志（非健康检查路径）
            if not request.url.path.startswith("/health") and not request.url.path.startswith("/metrics"):
                logger.info(
                    "api_request",
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                )

            response.headers["X-Request-ID"] = request_id
            return response

        except Exception:
            latency_ms = int((time.perf_counter() - start) * 1000)
            logger.exception("api_request_error", latency_ms=latency_ms)
            raise
