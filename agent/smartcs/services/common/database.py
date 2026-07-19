"""SQLAlchemy 异步数据库引擎与会话管理

使用 FastAPI app.state 管理连接池，支持依赖注入。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from smartcs.shared.config import get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI


async def init_db(app: FastAPI) -> None:
    """初始化数据库引擎和会话工厂，存储到 app.state

    生产级连接池配置:
    - pool_size / max_overflow 可通过环境变量配置
    - pool_recycle=3600: 1 小时回收连接，防止 PG/防火墙 idle timeout 断连
    - pool_pre_ping: 使用前检查连接活性
    - pool_reset_on_return: 归还连接时回滚未提交事务
    """
    settings = get_settings()
    engine = create_async_engine(
        settings.database.dsn,
        echo=settings.debug,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_reset_on_return="rollback",
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory


async def close_db(app: FastAPI) -> None:
    """关闭数据库引擎"""
    engine = getattr(app.state, "db_engine", None)
    if engine:
        await engine.dispose()
        app.state.db_engine = None


async def get_db(app: FastAPI) -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话（依赖注入用）"""
    session_factory: async_sessionmaker[AsyncSession] = app.state.db_session_factory
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
