"""Redis 异步连接池管理

使用 FastAPI app.state 管理连接池，支持依赖注入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from lumio.shared.config import get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI


async def init_redis(app: FastAPI) -> None:
    """初始化 Redis 连接池，存储到 app.state"""
    settings = get_settings()
    pool = aioredis.ConnectionPool.from_url(
        settings.redis.url,
        max_connections=settings.redis.max_connections,
        decode_responses=True,
    )
    client = aioredis.Redis(connection_pool=pool)
    app.state.redis_pool = pool
    app.state.redis_client = client


async def close_redis(app: FastAPI) -> None:
    """关闭 Redis 连接池"""
    pool: aioredis.ConnectionPool | None = getattr(app.state, "redis_pool", None)
    if pool:
        await pool.disconnect()
        app.state.redis_pool = None
    app.state.redis_client = None


def get_redis(app: FastAPI) -> aioredis.Redis:
    """获取 Redis 客户端实例（依赖注入用）"""
    return app.state.redis_client


# P0-2 第三轮修复: 独立于 app.state 的全局 Redis 客户端 (懒加载)
# 修复前 tool_robustness.py / budget.py 从本模块 import get_redis_client 必然 ImportError,
# 被 except 吞掉后配额/预算静默失效 (永远放行)。现提供真正的无参入口。
_global_redis: aioredis.Redis | None = None


def init_global_redis_client() -> aioredis.Redis:
    """初始化全局 Redis 客户端 (供 tool_robustness/budget 等后台组件使用)."""
    global _global_redis
    if _global_redis is not None:
        return _global_redis
    settings = get_settings()
    pool = aioredis.ConnectionPool.from_url(
        settings.redis.url,
        max_connections=settings.redis.max_connections,
        decode_responses=True,
    )
    _global_redis = aioredis.Redis(connection_pool=pool)
    return _global_redis


def get_redis_client() -> aioredis.Redis:
    """无参获取全局 Redis 客户端 (懒加载)."""
    if _global_redis is None:
        return init_global_redis_client()
    return _global_redis


async def close_global_redis_client() -> None:
    """关闭全局 Redis 客户端 (测试 teardown 用)."""
    global _global_redis
    if _global_redis is not None:
        try:
            await _global_redis.aclose()
        except Exception:
            pass
        _global_redis = None
