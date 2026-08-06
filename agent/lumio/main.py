"""智能客服平台 - FastAPI 应用入口

启动方式:
    # 开发模式（机器人服务）
    uvicorn lumio.main:bot_app --reload --port 8000

    # 开发模式（坐席辅助服务）
    uvicorn lumio.main:assist_app --reload --port 8001
"""

from __future__ import annotations

import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lumio.services.assist.app import create_assist_app
from lumio.services.assist.router import start_notify_worker, stop_notify_worker
from lumio.services.bot.app import create_bot_app
from lumio.services.bot.router import start_bot_worker, stop_bot_worker
from lumio.services.common.database import close_db, init_db, init_global_session_factory
from lumio.services.common.deps import (
    close_agent,
    close_assist_orchestrator,
    close_chat_svc_client,
    close_classifier,
    close_degradation_manager,
    close_dependency_breakers,
    close_elasticsearch,
    close_embedding,
    close_health_monitor,
    close_llm,
    close_mcp_client,
    close_milvus,
    close_minio,
    close_reranker,
    close_session_manager,
    close_session_timeout_manager,
    init_agent,
    init_assist_orchestrator,
    init_chat_svc_client,
    init_classifier,
    init_degradation_manager,
    init_dependency_breakers,
    init_elasticsearch,
    init_embedding,
    init_health_monitor,
    init_llm,
    init_mcp_client,
    init_milvus,
    init_minio,
    init_reranker,
    init_session_manager,
    init_session_timeout_manager,
    init_transfer_checker,
)
from lumio.services.common.gdpr import start_gdpr_sweep_worker, stop_gdpr_sweep_worker
from lumio.services.common.redis_client import (
    close_redis,
    init_global_redis_client,
    init_redis,
)
from lumio.shared.config import get_settings
from lumio.shared.logger import setup_logger
from lumio.shared.middleware import register_exception_handlers


class _SuppressExceptions:
    """上下文管理器：抑制异常并记录日志，用于关闭阶段避免一个失败阻塞后续清理"""

    def __init__(self, logger_obj: logging.Logger):
        self._logger = logger_obj

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val is not None:
            self._logger.warning("关闭步骤异常（已忽略）: %s", exc_val)
        return True


def _safe_init_global_factory() -> None:
    """同步初始化全局 session factory (供决策日志 PG 落库用).

    异常不抛出: PG 不可用时降级到 Redis-only, 不影响服务启动.
    """
    import logging

    try:
        init_global_session_factory()
    except Exception as exc:
        logging.getLogger("lumio.main").warning(
            "全局 session factory 初始化失败 (决策日志降级为 Redis-only): %s", exc
        )


def _safe_init_global_redis() -> None:
    """P0-2 第三轮修复: 启动时初始化全局 Redis 客户端 (配额/预算后台组件用).

    异常不抛出: 连接失败由各组件懒加载兜底 + WARNING 日志.
    """
    import logging

    try:
        init_global_redis_client()
    except Exception as exc:
        logging.getLogger("lumio.main").warning(
            "全局 Redis 客户端初始化失败 (配额/预算将懒加载重试): %s", exc
        )


def _safe_start_gdpr_worker() -> None:
    """P0-4: 启动 GDPR 调度 worker (消费到期硬删除). 异常不抛出."""
    import logging

    try:
        start_gdpr_sweep_worker()
    except Exception as exc:
        logging.getLogger("lumio.main").warning("GDPR worker 启动失败: %s", exc)


def _safe_build_alert_rules() -> None:
    """P0-6 第三轮修复: 接线告警规则 (此前 build_alert_rules 全仓库无调用点, 告警死代码).
    每分钟扫描 LLM 错误率 / 预算超限指标, 触发 P0/P1 告警. 异常不抛出."""
    import logging

    try:
        from lumio.shared.alerting import build_alert_rules, get_alert_router

        build_alert_rules(get_alert_router())
    except Exception as exc:
        logging.getLogger("lumio.main").warning("告警规则接线失败: %s", exc)


# ── 公共初始化/关闭步骤（两个服务共享的基础设施）──
# AI 能力 (LLM/Embedding/Reranker) 由编排层 HTTP 直连外部模型服务 (Ollama/TEI),
# 经 Higress AI 网关统一治理; 不设独立 gRPC 能力层 (曾规划, 已移除 — 规模未到, 网关已覆盖治理).
_COMMON_INIT_STEPS = [
    init_db,
    init_redis,
    init_elasticsearch,
    init_milvus,
    init_minio,
    init_dependency_breakers,
    init_embedding,
    init_reranker,
    init_llm,
    init_session_manager,
    init_classifier,
    # P1-7 决策日志 PG 落库: 启动时初始化全局 session factory
    lambda _app: _safe_init_global_factory(),
    # P0-2 第三轮修复: 启动时初始化全局 Redis 客户端 (tool_robustness/budget 后台组件)
    lambda _app: _safe_init_global_redis(),
]

_COMMON_CLOSE_STEPS = [
    close_classifier,
    close_session_manager,
    close_llm,
    close_reranker,
    close_embedding,
    close_dependency_breakers,
    close_minio,
    close_milvus,
    close_elasticsearch,
    close_redis,
    close_db,
]

# 机器人服务启动/关闭步骤 = 公共步骤 + Bot 专有步骤
# R1 第三轮修复: 之前用 _COMMON_INIT_STEPS[:10] 切片, 漏掉 init_classifier(第11位)
# 和 _safe_init_global_factory(第12位), 且单独追加 init_session_manager 导致重复初始化 2 次.
_BOT_INIT_STEPS = [
    *_COMMON_INIT_STEPS,  # 全量 14 步 (init_db ... 全局 redis)
    init_health_monitor,
    init_degradation_manager,
    init_transfer_checker,
    init_chat_svc_client,
    init_mcp_client,
    init_agent,
    start_bot_worker,
    # P0-4 第三轮修复: GDPR 调度 worker (消费到期硬删除)
    lambda _app: _safe_start_gdpr_worker(),
    # P0-6 第三轮修复: 告警规则接线 (LLM 错误率/预算超限 → P0/P1 告警)
    lambda _app: _safe_build_alert_rules(),
]

_BOT_CLOSE_STEPS = [
    stop_bot_worker,
    close_chat_svc_client,
    close_agent,
    close_mcp_client,
    *_COMMON_CLOSE_STEPS[:2],  # close_classifier, close_session_manager
    close_degradation_manager,
    close_health_monitor,
    stop_gdpr_sweep_worker,  # P0-4: 停 GDPR 调度 worker
    *_COMMON_CLOSE_STEPS[2:],  # close_llm ... close_db
]


@asynccontextmanager
async def bot_lifespan(app: FastAPI):
    """机器人服务生命周期"""
    settings = get_settings()
    logger = setup_logger("lumio.bot", settings.log_level, json_format=settings.environment == "production")
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
                    with _SuppressExceptions(logger):
                        await close_step(app)
                    break
        raise

    yield

    logger.info("机器人服务关闭中...")
    for step in _BOT_CLOSE_STEPS:
        with _SuppressExceptions(logger):
            await step(app)
    logger.info("机器人服务已关闭")


# 坐席辅助服务启动/关闭步骤
async def _init_assist_ws_pool(app: FastAPI) -> None:
    """初始化 WebSocket 连接池"""
    app.state.assist_ws_pool = {}


async def _close_assist_ws_pool(app: FastAPI) -> None:
    """清理 WebSocket 连接池"""
    ws_pool: dict = getattr(app.state, "assist_ws_pool", {})
    for ws in list(ws_pool.values()):
        with contextlib.suppress(Exception):
            await ws.close()
    ws_pool.clear()


_ASSIST_INIT_STEPS = [
    *_COMMON_INIT_STEPS,  # R1 第三轮修复: 全量 (原 [:10] 漏 init_classifier/全局 factory/全局 redis)
    init_session_timeout_manager,
    init_assist_orchestrator,
    _init_assist_ws_pool,
    start_notify_worker,
]

_ASSIST_CLOSE_STEPS = [
    stop_notify_worker,
    _close_assist_ws_pool,
    close_assist_orchestrator,
    *_COMMON_CLOSE_STEPS[:2],  # close_classifier, close_session_manager
    close_session_timeout_manager,
    *_COMMON_CLOSE_STEPS[2:],  # close_llm ... close_db
]


@asynccontextmanager
async def assist_lifespan(app: FastAPI):
    """坐席辅助服务生命周期"""
    settings = get_settings()
    logger = setup_logger("lumio.assist", settings.log_level, json_format=settings.environment == "production")
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
                    with _SuppressExceptions(logger):
                        await close_step(app)
                    break
        raise

    yield

    logger.info("坐席辅助服务关闭中...")
    for step in _ASSIST_CLOSE_STEPS:
        with _SuppressExceptions(logger):
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

    uvicorn.run("lumio.main:bot_app", host=get_settings().service_host, port=8000, reload=True)
