"""智能客服平台 - FastAPI 应用入口

启动方式:
    # 开发模式（机器人服务）
    uvicorn smartcs.main:bot_app --reload --port 8000

    # 开发模式（坐席辅助服务）
    uvicorn smartcs.main:assist_app --reload --port 8001
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from smartcs.services.bot.app import create_bot_app
from smartcs.services.assist.app import create_assist_app
from smartcs.shared.config import get_settings
from smartcs.shared.logger import setup_logger
from smartcs.shared.middleware import register_exception_handlers
from smartcs.services.common.database import init_db, close_db
from smartcs.services.common.redis_client import init_redis, close_redis
from smartcs.services.common.grpc_clients import init_grpc_channels, close_grpc_channels


@asynccontextmanager
async def bot_lifespan(app: FastAPI):
    """机器人服务生命周期"""
    settings = get_settings()
    logger = setup_logger("smartcs.bot", settings.log_level, json_format=settings.environment == "production")
    logger.info("机器人服务启动中...")

    await init_db(app)
    await init_redis(app)
    await init_grpc_channels(app)
    logger.info("机器人服务就绪")

    yield

    logger.info("机器人服务关闭中...")
    await close_grpc_channels(app)
    await close_redis(app)
    await close_db(app)
    logger.info("机器人服务已关闭")


@asynccontextmanager
async def assist_lifespan(app: FastAPI):
    """坐席辅助服务生命周期"""
    settings = get_settings()
    logger = setup_logger("smartcs.assist", settings.log_level, json_format=settings.environment == "production")
    logger.info("坐席辅助服务启动中...")

    await init_db(app)
    await init_redis(app)
    await init_grpc_channels(app)
    logger.info("坐席辅助服务就绪")

    yield

    logger.info("坐席辅助服务关闭中...")
    await close_grpc_channels(app)
    await close_redis(app)
    await close_db(app)
    logger.info("坐席辅助服务已关闭")


# 创建两个独立服务实例
bot_app = create_bot_app(lifespan=bot_lifespan)
assist_app = create_assist_app(lifespan=assist_lifespan)

# 注册全局异常处理器
register_exception_handlers(bot_app)
register_exception_handlers(assist_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("smartcs.main:bot_app", host="0.0.0.0", port=8000, reload=True)
