"""Redis 异步连接池管理

使用 FastAPI app.state 管理连接池，支持依赖注入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from smartcs.shared.config import get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI


async def init_redis(app: FastAPI) -> None:
    """初始化 Redis 连接池，存储到 app.state"""
    settings = get_settings()
    pool = aioredis.ConnectionPool.from_url(
        settings.redis.url,
        max_connections=20,
        decode_responses=True,
    )
    app.state.redis_pool = pool
    app.state.redis_client = aioredis.Redis(connection_pool=pool)


async def close_redis(app: FastAPI) -> None:
    """关闭 Redis 连接池"""
    pool: aioredis.ConnectionPool | None = getattr(app.state, "redis_pool", None)
    if pool:
        await pool.disconnect()
        app.state.redis_pool = None


def get_redis(app: FastAPI) -> aioredis.Redis:
    """获取 Redis 客户端实例（依赖注入用）"""
    pool: aioredis.ConnectionPool = app.state.redis_pool
    return aioredis.Redis(connection_pool=pool)
