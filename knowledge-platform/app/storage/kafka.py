"""Kafka 生产者/消费者管理

Kafka 职责：
1. kp.ingest.request — API 投递 ETL 任务，Worker 消费执行
2. kp.ingest.result — ETL 完成后发布事件，下游消费（缓存失效、索引刷新、审计）
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from app.config import get_settings

logger = logging.getLogger(__name__)

_producer: AIOKafkaProducer | None = None


async def init_producer() -> AIOKafkaProducer:
    """初始化 Kafka 生产者"""
    global _producer
    settings = get_settings()
    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka.bootstrap_servers,
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
    )
    await _producer.start()
    logger.info("Kafka 生产者已启动: %s", settings.kafka.bootstrap_servers)
    return _producer


async def close_producer() -> None:
    """关闭 Kafka 生产者"""
    global _producer
    if _producer:
        await _producer.stop()
        _producer = None
        logger.info("Kafka 生产者已关闭")


def get_producer() -> AIOKafkaProducer | None:
    """获取 Kafka 生产者"""
    return _producer


async def publish_ingest_request(doc_id: str, payload: dict[str, Any]) -> None:
    """投递 ETL 任务到 Kafka

    Args:
        doc_id: 文档 ID（作为 Kafka key，保证同文档顺序）
        payload: 任务载荷（file_path, source_type, metadata 等）
    """
    producer = get_producer()
    if producer is None:
        raise RuntimeError("Kafka 生产者未初始化")

    settings = get_settings()
    await producer.send_and_wait(
        settings.kafka.ingest_topic,
        value=payload,
        key=doc_id,
    )
    logger.info("ETL 任务已投递: doc_id=%s, topic=%s", doc_id, settings.kafka.ingest_topic)


async def publish_ingest_result(doc_id: str, status: str, detail: dict[str, Any] | None = None) -> None:
    """发布 ETL 完成事件

    Args:
        doc_id: 文档 ID
        status: COMPLETED / FAILED
        detail: 结果详情（chunk_count, latency 等）
    """
    producer = get_producer()
    if producer is None:
        logger.warning("Kafka 生产者未初始化，跳过结果发布")
        return

    settings = get_settings()
    payload = {"doc_id": doc_id, "status": status, "detail": detail or {}}
    await producer.send_and_wait(
        settings.kafka.result_topic,
        value=payload,
        key=doc_id,
    )
    logger.info("ETL 结果已发布: doc_id=%s, status=%s", doc_id, status)


async def publish_to_dlq(doc_id: str, message: dict, error: str) -> None:
    """投递死信队列（消息处理失败超过重试次数时调用）

    Args:
        doc_id: 文档 ID
        message: 原始消息内容
        error: 错误信息
    """
    producer = get_producer()
    if producer is None:
        logger.error("Kafka 生产者未初始化，无法投递 DLQ: doc_id=%s", doc_id)
        return

    settings = get_settings()
    dlq_payload = {
        "doc_id": doc_id,
        "original_message": message,
        "error": error,
        "failed_at": _utc_now_iso(),
    }
    await producer.send_and_wait(
        settings.kafka.dlq_topic,
        value=dlq_payload,
        key=doc_id,
    )
    logger.error("消息已投递死信队列: doc_id=%s, topic=%s", doc_id, settings.kafka.dlq_topic)


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def create_consumer() -> AIOKafkaConsumer:
    """创建 Kafka 消费者（Worker 进程使用）"""
    settings = get_settings()
    return AIOKafkaConsumer(
        settings.kafka.ingest_topic,
        bootstrap_servers=settings.kafka.bootstrap_servers,
        group_id=settings.kafka.consumer_group,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        key_deserializer=lambda k: k.decode("utf-8") if k else None,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
