"""限流中间件 — 基于 Redis 滑动窗口

生产级限流：Redis + Lua 原子操作，支持分布式部署。
降级策略：Redis 不可用时放行（不阻塞业务）。
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.logging import get_logger

logger = get_logger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis 滑动窗口限流

    每个 IP + 路径组合一个限流桶。
    超限返回 429 Too Many Requests。
    """

    def __init__(self, app, requests_per_minute: int = 60) -> None:
        super().__init__(app)
        self._limit = requests_per_minute
        self._window = 60  # 秒

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 健康检查/指标端点不限流
        path = request.url.path
        if path.startswith("/health") or path.startswith("/metrics"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        key = f"kp:ratelimit:{client_ip}:{path}"

        # 获取 Redis
        from app.storage.redis import get_redis

        redis = get_redis()
        if redis is None:
            # Redis 不可用，放行
            return await call_next(request)

        try:
            # 滑动窗口：ZSET + 时间戳
            now = time.time()
            pipe = redis.pipeline()
            pipe.zadd(key, {str(now): now})
            pipe.zremrangebyscore(key, 0, now - self._window)
            pipe.zcard(key)
            pipe.expire(key, self._window)
            results = await pipe.execute()

            count = results[2]
            if count > self._limit:
                logger.warning("限流触发", client_ip=client_ip, path=path, count=count, limit=self._limit)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "请求过于频繁，请稍后重试", "retry_after": self._window},
                    headers={"Retry-After": str(self._window)},
                )

        except Exception:
            logger.debug("限流检查失败，放行")

        return await call_next(request)
