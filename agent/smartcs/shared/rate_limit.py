"""请求限流配置

使用 slowapi 实现限流，Redis 作为共享存储后端。
- 已认证用户按 user_id 限流，未认证按 IP 限流
- 健康检查端点豁免
- 支持按端点差异化限流（通过 @limiter.limit 装饰器）
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from smartcs.shared.config import get_settings


def _user_or_ip_key(request) -> str:
    """复合限流 key: 已认证用户用 user_id，未认证用 IP

    避免同一 NAT/代理后多用户共享限流配额。
    """
    # 尝试从 JWT 提取 user_id
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from smartcs.shared.auth import decode_token

            payload = decode_token(auth_header[7:])
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except Exception:
            pass
    return f"ip:{get_remote_address(request)}"


# 豁免限流的路径前缀
_EXEMPT_PATHS = ("/api/health", "/metrics", "/favicon.ico")


def create_limiter() -> Limiter:
    """创建限流器实例

    使用 Redis URI 作为存储后端，支持多实例共享限流计数。
    """
    settings = get_settings()

    if not settings.rate_limit_enabled:
        return Limiter(key_func=_user_or_ip_key, enabled=False)

    # 构建 Redis URI
    redis_url = f"redis://{settings.redis.host}:{settings.redis.port}"
    if settings.redis.password:
        redis_url = f"redis://:{settings.redis.password}@{settings.redis.host}:{settings.redis.port}"
    redis_url += f"/{settings.redis.db}"

    return Limiter(
        key_func=_user_or_ip_key,
        storage_uri=redis_url,
        default_limits=[settings.rate_limit_default],
    )


def is_rate_limit_exempt(path: str) -> bool:
    """判断请求路径是否豁免限流"""
    return any(path.startswith(prefix) for prefix in _EXEMPT_PATHS)
