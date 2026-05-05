"""智能客服平台 - FastAPI 应用入口

启动方式:
    # 开发模式（机器人服务）
    uvicorn smartcs.main:bot_app --reload --port 8000

    # 开发模式（坐席辅助服务）
    uvicorn smartcs.main:assist_app --reload --port 8001
"""

from __future__ import annotations

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
    close_state_manager,
    close_temporal_client,
    close_temporal_worker,
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
    init_state_manager,
    init_temporal_client,
    init_temporal_worker,
    init_transfer_checker,
)
from smartcs.services.common.grpc_clients import close_grpc_channels, init_grpc_channels
from smartcs.services.bot.router import start_chat_worker, stop_chat_worker
from smartcs.services.common.redis_client import close_redis, init_redis
from smartcs.shared.config import get_settings
from smartcs.shared.logger import setup_logger
from smartcs.shared.middleware import register_exception_handlers


class _suppress_exceptions:
    """上下文管理器：抑制异常并记录日志，用于关闭阶段避免一个失败阻塞后续清理"""

    def __init__(self, logger_obj: logging.Logger):
        self._logger = logger_obj

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val is not None:
            self._logger.warning("关闭步骤异常（已忽略）: %s", exc_val)
        return True


import logging

# 机器人服务启动/关闭步骤（按依赖顺序）
_BOT_INIT_STEPS = [
    init_db,
    init_redis,
    init_elasticsearch,
    init_milvus,
    init_minio,
    init_embedding,
    init_reranker,
    init_grpc_channels,
    init_llm,
    init_health_monitor,
    init_degradation_manager,
    init_session_manager,
    init_classifier,
    init_transfer_checker,
    init_star_client,
    init_agent,
    start_chat_worker,
]

_BOT_CLOSE_STEPS = [
    stop_chat_worker,
    close_star_client,
    close_agent,
    close_classifier,
    close_session_manager,
    close_degradation_manager,
    close_health_monitor,
    close_llm,
    close_grpc_channels,
    close_reranker,
    close_embedding,
    close_minio,
    close_milvus,
    close_elasticsearch,
    close_redis,
    close_db,
]


@asynccontextmanager
async def bot_lifespan(app: FastAPI):
    """机器人服务生命周期"""
    settings = get_settings()
    logger = setup_logger("smartcs.bot", settings.log_level, json_format=settings.environment == "production")
    logger.info("机器人服务启动中...")

    initialized: list[tuple[str, object]] = []
    try:
        for step in _BOT_INIT_STEPS:
            await step(app)
            initialized.append((step.__name__, app))
        logger.info("机器人服务就绪")
    except Exception:
        # 启动失败：按逆序清理已初始化的资源，避免泄漏
        logger.exception("机器人服务启动失败，正在清理已初始化的资源...")
        for step_name, _ in reversed(initialized):
            close_fn_name = step_name.replace("init_", "close_").replace("start_", "stop_")
            for close_step in _BOT_CLOSE_STEPS:
                if close_step.__name__ == close_fn_name:
                    with _suppress_exceptions(logger):
                        await close_step(app)
                    break
        raise

    yield

    logger.info("机器人服务关闭中...")
    for step in _BOT_CLOSE_STEPS:
        with _suppress_exceptions(logger):
            await step(app)
    logger.info("机器人服务已关闭")


# 坐席辅助服务启动/关闭步骤
async def _init_assist_ws_pool(app: FastAPI) -> None:
    """初始化 WebSocket 连接池"""
    app.state.assist_ws_connections = {}


async def _close_assist_ws_pool(app: FastAPI) -> None:
    """清理 WebSocket 连接池"""
    ws_pool: dict = getattr(app.state, "assist_ws_connections", {})
    for ws in list(ws_pool.values()):
        try:
            await ws.close()
        except Exception:
            pass
    ws_pool.clear()


_ASSIST_INIT_STEPS = [
    init_db,
    init_redis,
    init_elasticsearch,
    init_milvus,
    init_minio,
    init_embedding,
    init_reranker,
    init_grpc_channels,
    init_llm,
    init_session_manager,
    init_classifier,
    init_assist_orchestrator,
    init_state_manager,
    init_temporal_client,
    init_temporal_worker,
    _init_assist_ws_pool,
]

_ASSIST_CLOSE_STEPS = [
    _close_assist_ws_pool,
    close_temporal_worker,
    close_temporal_client,
    close_state_manager,
    close_assist_orchestrator,
    close_classifier,
    close_session_manager,
    close_llm,
    close_grpc_channels,
    close_reranker,
    close_embedding,
    close_minio,
    close_milvus,
    close_elasticsearch,
    close_redis,
    close_db,
]


@asynccontextmanager
async def assist_lifespan(app: FastAPI):
    """坐席辅助服务生命周期"""
    settings = get_settings()
    logger = setup_logger("smartcs.assist", settings.log_level, json_format=settings.environment == "production")
    logger.info("坐席辅助服务启动中...")

    initialized: list[tuple[str, object]] = []
    try:
        for step in _ASSIST_INIT_STEPS:
            await step(app)
            initialized.append((step.__name__, app))
        logger.info("坐席辅助服务就绪")
    except Exception:
        # 启动失败：按逆序清理已初始化的资源，避免泄漏
        logger.exception("坐席辅助服务启动失败，正在清理已初始化的资源...")
        for step_name, _ in reversed(initialized):
            close_fn_name = step_name.replace("init_", "close_").replace("start_", "stop_")
            for close_step in _ASSIST_CLOSE_STEPS:
                if close_step.__name__ == close_fn_name:
                    with _suppress_exceptions(logger):
                        await close_step(app)
                    break
        raise

    yield

    logger.info("坐席辅助服务关闭中...")
    for step in _ASSIST_CLOSE_STEPS:
        with _suppress_exceptions(logger):
            await step(app)
    logger.info("坐席辅助服务已关闭")


# 创建两个独立服务实例
bot_app = create_bot_app(lifespan=bot_lifespan)
assist_app = create_assist_app(lifespan=assist_lifespan)

# 注册全局异常处理器
register_exception_handlers(bot_app)
register_exception_handlers(assist_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("smartcs.main:bot_app", host=get_settings().service_host, port=8000, reload=True)
