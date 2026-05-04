"""智能客服平台 - FastAPI 应用入口

启动方式:
    # 开发模式（机器人服务）
    uvicorn smartcs.main:bot_app --reload --port 8000

    # 开发模式（坐席辅助服务）
    uvicorn smartcs.main:assist_app --reload --port 8001
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from smartcs.services.assist.app import create_assist_app
from smartcs.services.bot.app import create_bot_app
from smartcs.services.common.database import close_db, init_db
from smartcs.services.common.deps import (
    close_agent,
    close_assist_orchestrator,
    close_classifier,
    close_degradation_manager,
    close_elasticsearch,
    close_embedding,
    close_health_monitor,
    close_llm,
    close_milvus,
    close_minio,
    close_reranker,
    close_session_manager,
    close_star_client,
    init_agent,
    init_assist_orchestrator,
    init_classifier,
    init_degradation_manager,
    init_elasticsearch,
    init_embedding,
    init_health_monitor,
    init_llm,
    init_milvus,
    init_minio,
    init_reranker,
    init_session_manager,
    init_star_client,
    init_transfer_checker,
)
from smartcs.services.common.grpc_clients import close_grpc_channels, init_grpc_channels
from smartcs.services.bot.router import start_chat_worker, stop_chat_worker
from smartcs.services.common.redis_client import close_redis, init_redis
from smartcs.shared.config import get_settings
from smartcs.shared.logger import setup_logger
from smartcs.shared.middleware import register_exception_handlers


@asynccontextmanager
async def bot_lifespan(app: FastAPI):
    """机器人服务生命周期"""
    settings = get_settings()
    logger = setup_logger("smartcs.bot", settings.log_level, json_format=settings.environment == "production")
    logger.info("机器人服务启动中...")

    await init_db(app)
    await init_redis(app)
    await init_elasticsearch(app)
    await init_milvus(app)
    await init_minio(app)
    await init_embedding(app)
    await init_reranker(app)
    await init_grpc_channels(app)
    await init_llm(app)
    await init_health_monitor(app)       # 启动后台健康探测（依赖 llm_client）
    await init_degradation_manager(app)  # 初始化降级管理器（依赖 llm_client + health_monitor）
    await init_session_manager(app)
    await init_classifier(app)
    await init_transfer_checker(app)
    await init_star_client(app)          # star-connection HTTP 客户端（转人工桥接）
    await init_agent(app)                # 依赖 degradation_manager
    await start_chat_worker(app)         # 启动聊天队列后台工作器
    logger.info("机器人服务就绪")

    yield

    logger.info("机器人服务关闭中...")
    await stop_chat_worker(app)
    await close_star_client(app)
    await close_agent(app)
    await close_classifier(app)
    await close_session_manager(app)
    await close_degradation_manager(app)
    await close_health_monitor(app)       # 停止后台健康探测
    await close_llm(app)
    await close_grpc_channels(app)
    await close_reranker(app)
    await close_embedding(app)
    await close_minio(app)
    await close_milvus(app)
    await close_elasticsearch(app)
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
    await init_elasticsearch(app)
    await init_milvus(app)
    await init_minio(app)
    await init_embedding(app)
    await init_reranker(app)
    await init_grpc_channels(app)
    await init_llm(app)
    await init_session_manager(app)
    await init_assist_orchestrator(app)
    logger.info("坐席辅助服务就绪")

    yield

    logger.info("坐席辅助服务关闭中...")
    await close_assist_orchestrator(app)
    await close_session_manager(app)
    await close_llm(app)
    await close_grpc_channels(app)
    await close_reranker(app)
    await close_embedding(app)
    await close_minio(app)
    await close_milvus(app)
    await close_elasticsearch(app)
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
