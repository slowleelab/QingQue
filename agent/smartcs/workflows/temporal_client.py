"""Temporal Client 连接管理"""
from __future__ import annotations

import logging

from temporalio.client import Client

from smartcs.shared.config import get_settings

logger = logging.getLogger(__name__)

_client: Client | None = None


async def get_temporal_client() -> Client:
    """获取或创建 Temporal Client 单例"""
    global _client
    if _client is None:
        settings = get_settings()
        _client = await Client.connect(
            target_host=f"{settings.temporal.host}:{settings.temporal.port}",
            namespace=settings.temporal.namespace,
        )
        logger.info("Temporal Client 连接成功: %s:%d", settings.temporal.host, settings.temporal.port)
    return _client


async def close_temporal_client() -> None:
    """关闭 Temporal Client"""
    global _client
    if _client is not None:
        _client = None
        logger.info("Temporal Client 已关闭")
