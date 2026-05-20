"""Temporal Worker 启动入口"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from smartcs.shared.config import get_settings

logger = logging.getLogger(__name__)


async def start_worker(client: Client) -> asyncio.Task:
    """启动 Temporal Worker（后台任务）

    Returns:
        asyncio.Task: Worker 运行任务，可通过取消此任务来停止 Worker
    """
    settings = get_settings()

    # 延迟导入避免循环依赖
    from smartcs.services.common.redis_client import get_redis
    from smartcs.workflows.activities import (
        cas_write_state,
        evaluate_d1_service,
        evaluate_d2_marketing,
        evaluate_d3_risk,
        execute_e1_ai_service,
        execute_e2_marketing,
        execute_e3_risk,
        read_state_snapshot,
        set_redis_for_activities,
    )
    from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow
    try:
        # 尝试获取已初始化的 Redis 实例
        from smartcs.main import _get_assist_app
        app = _get_assist_app()
        if app:
            redis = get_redis(app)
            set_redis_for_activities(redis)
    except Exception as e:
        logger.warning("Worker 无法注入 Redis: %s，状态管理 Activity 将降级", e)

    worker = Worker(
        client=client,
        task_queue=settings.temporal.task_queue,
        workflows=[OrchestrationWorkflow],
        activities=[
            evaluate_d1_service,
            evaluate_d2_marketing,
            evaluate_d3_risk,
            execute_e1_ai_service,
            execute_e2_marketing,
            execute_e3_risk,
            read_state_snapshot,
            cas_write_state,
        ],
    )

    logger.info("Temporal Worker 启动: task_queue=%s", settings.temporal.task_queue)
    task = asyncio.create_task(worker.run())
    return task


async def stop_worker(worker_task: asyncio.Task | None) -> None:
    """停止 Temporal Worker"""
    if worker_task is not None and not worker_task.done():
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
        logger.info("Temporal Worker 已停止")
