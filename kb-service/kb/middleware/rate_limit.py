"""限流中间件 — I2-C1 分级限流 (tier × path_group)

生产级限流: Redis Lua 原子 INCR + EXPIRE.
维度: principal.tier (vip/normal/internal) × path group (read/write/admin)
超限: 429 + Retry-After + X-RateLimit-Limit/Remaining/Reset
降级: Redis 不可用时 fail-open + WARN 日志 (生产可观测)

中间件链顺序 (外→内): CORS → Audit → RateLimit → Prometheus → routes
注意: principal 注入由 verify_principal (Depends) 完成, 在路由层才生效.
中间件层 best-effort 解析 JWT 取 tier/actor_id, 失败则走 IP 兜底.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from kb.config import get_settings
from kb.logging import get_logger
from kb.middleware.prometheus import RATE_LIMIT_EXCEEDED
from kb.security.rate_limiter import (
    check_rate_limit,
    extract_context,
    get_ip_fallback_key,
    get_limit_key,
    get_tier_limit,
)

logger = get_logger(__name__)


# 不限流的白名单
_WHITELIST_PREFIXES = ("/health", "/metrics", "/docs", "/redoc", "/openapi.json")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """分级限流中间件 (I2-C1)

    关键改进:
    - 维度: tier + path_group, 不再被路径参数 (doc_id 等) 绕过
    - 配额: tier_quotas 配置, fallback rate_limit_per_minute
    - 响应: 429 + Retry-After + 3 个 X-RateLimit-* 头
    - 降级: Redis 不可用 → fail-open + WARN 日志 (原实现是静默 debug)
    - 监控: RATE_LIMIT_EXCEEDED{tier, path_group} 计数器
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 白名单
        path = request.url.path
        for prefix in _WHITELIST_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                return await call_next(request)

        settings = get_settings()
        if not settings.security.rate_limit_enabled:
            return await call_next(request)

        # 抽取限流上下文
        ctx = extract_context(request, settings)
        limit = get_tier_limit(ctx.tier, ctx.group, settings)

        # 构造 key (认证用户用 actor_id, anon 走 IP 兜底)
        if ctx.is_authenticated:
            key = get_limit_key(
                tier=ctx.tier,
                actor_id=ctx.actor_id,
                group=ctx.group,
                prefix=settings.security.rate_limit_redis_prefix,
            )
        else:
            key = get_ip_fallback_key(
                client_ip=ctx.actor_id,
                group=ctx.group,
                prefix=settings.security.rate_limit_redis_prefix,
            )

        # 取 Redis
        from kb.storage.redis import get_redis

        redis = get_redis()
        if redis is None:
            # Redis 不可用 — fail-open + WARN
            logger.warning("限流检查跳过 (Redis 不可用)")
            return await call_next(request)

        window = settings.security.rate_limit_window_seconds
        try:
            allowed, current, retry_after = await check_rate_limit(
                redis, key=key, limit=limit, window=window
            )
        except Exception as e:
            # Redis 命令失败 — fail-open + WARN (生产可观察)
            logger.warning("限流检查失败 (Redis 命令异常): %s", e)
            return await call_next(request)

        if not allowed:
            RATE_LIMIT_EXCEEDED.labels(tier=ctx.tier, path_group=ctx.group.value).inc()
            logger.warning(
                "rate_limit_exceeded",
                tier=ctx.tier,
                actor_id=ctx.actor_id,
                path_group=ctx.group.value,
                current=current,
                limit=limit,
                retry_after=retry_after,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后重试", "retry_after": retry_after},
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                },
            )

        # 成功: 注入 X-RateLimit-* 头供客户端观察
        response = await call_next(request)
        remaining = max(limit - current, 0)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(retry_after)
        return response
