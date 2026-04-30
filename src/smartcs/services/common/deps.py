"""统一依赖注入工厂

提供 FastAPI Depends 使用的依赖注入函数。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from smartcs.services.common.database import get_db
from smartcs.services.common.redis_client import get_redis


async def _get_app(request: Request):
    """从 Request 中获取 FastAPI app 实例"""
    return request.app


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话（FastAPI 依赖注入）"""
    async for session in get_db(request.app):
        yield session


def get_redis_client(request: Request) -> Redis:
    """获取 Redis 客户端（FastAPI 依赖注入）"""
    return get_redis(request.app)


# 类型别名，方便在路由中使用
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis_client)]
