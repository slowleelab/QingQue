"""初始化数据库表

使用 SQLAlchemy create_all 创建所有表。
生产环境应使用 alembic 迁移。

使用方式: python scripts/init_database.py
"""

import asyncio

from app.database import close_engine, get_engine
from app.orm import Base


async def init_database() -> None:
    print("创建数据库表...")
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("数据库表创建完成!")
    await close_engine()


if __name__ == "__main__":
    asyncio.run(init_database())
