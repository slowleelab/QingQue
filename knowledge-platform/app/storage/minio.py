"""MinIO 客户端管理 — 原始文档存储"""

from __future__ import annotations

import logging

from minio import Minio

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: Minio | None = None


def init_minio() -> Minio | None:
    """初始化 MinIO 客户端"""
    global _client
    settings = get_settings()
    try:
        client = Minio(
            endpoint=settings.minio.endpoint,
            access_key=settings.minio.access_key,
            secret_key=settings.minio.secret_key,
            secure=settings.minio.secure,
        )
        if not client.bucket_exists(settings.minio.bucket):
            client.make_bucket(settings.minio.bucket)
            logger.info("MinIO bucket 已创建: %s", settings.minio.bucket)
        _client = client
        logger.info("MinIO 连接成功: %s", settings.minio.endpoint)
        return client
    except Exception as e:
        logger.warning("MinIO 连接失败: %s", e)
        _client = None
        return None


def get_minio() -> Minio | None:
    """获取 MinIO 客户端"""
    return _client
