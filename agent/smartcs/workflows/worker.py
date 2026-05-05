"""Temporal Worker 启动入口"""
from __future__ import annotations

import asyncio
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
    from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow
    from smartcs.workflows.activities import (
        evaluate_d1_service,
        evaluate_d2_marketing,
        evaluate_d3_risk,
        execute_e1_ai_service,
        execute_e2_marketing,
        execute_e3_risk,
    )

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
        ],
    )

    logger.info("Temporal Worker 启动: task_queue=%s", settings.temporal.task_queue)
    task = asyncio.create_task(worker.run())
    return task


async def stop_worker(worker_task: asyncio.Task | None) -> None:
    """停止 Temporal Worker"""
    if worker_task is not None and not worker_task.done():
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        logger.info("Temporal Worker 已停止")
