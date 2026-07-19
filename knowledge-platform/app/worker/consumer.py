"""Kafka Worker 消费者

消费 kp.ingest.request 主题，执行异步 ETL 管线。
独立进程运行：python -m app.worker.consumer 或 kp-worker

流程：消费消息 → 从 MinIO 拉取原始文档 → 并发锁 → 调用 ingest_document() → 发布结果事件
失败策略：Redis 持久化重试计数，超过 max_retries 后投递死信队列
优雅停机：SIGTERM/SIGINT 信号触发 drain，等待 in-flight 消息处理完成
"""

from __future__ import annotations

import asyncio
import os
import signal
import tempfile

from app.config import get_settings
from app.database import close_engine, get_session_factory
from app.logging import bind_context, clear_context, configure_logging, get_logger
from app.orm.kb import KbSourceType
from app.pipeline.embedder import EmbeddingCircuitBreaker, create_embedding_provider
from app.pipeline.extractor import LLMExtractor
from app.pipeline.orchestrator import ingest_document
from app.storage.elasticsearch import close_es, get_es, init_es
from app.storage.kafka import (
    close_producer,
    create_consumer,
    init_producer,
    publish_ingest_result,
    publish_to_dlq,
)
from app.storage.minio import get_minio, init_minio
from app.storage.redis import (
    acquire_lock,
    clear_retry,
    get_retry_count,
    increment_retry,
    release_lock,
)

logger = get_logger(__name__)

# 优雅停机标志
_shutting_down = asyncio.Event()


async def process_message(
    message: dict,
    embedding_breaker: EmbeddingCircuitBreaker,
    llm_extractor: LLMExtractor,
) -> None:
    """处理单条 Kafka 消息"""
    doc_id = message.get("doc_id")
    file_path = message.get("file_path")
    source_type = message.get("source_type", "MARKDOWN")
    metadata = message.get("metadata", {})

    if not doc_id or not file_path:
        logger.error("消息缺少 doc_id 或 file_path", message=message)
        return

    bind_context(doc_id=doc_id, file_path=file_path)

    # ── 并发锁：防止同一文档被多个 Worker 同时处理 ──
    settings = get_settings()
    lock_token = await acquire_lock(f"ingest:{doc_id}", ttl=settings.rag.ingestion_lock_ttl)
    if lock_token is None:
        logger.warning("文档正在被其他 Worker 处理，跳过", doc_id=doc_id)
        return

    try:
        logger.info("开始处理文档")

        # 从 MinIO 拉取文档
        minio_client = get_minio()
        if minio_client is None:
            logger.error("MinIO 不可用")
            await publish_ingest_result(doc_id, "FAILED", {"error": "MinIO unavailable"})
            return

        try:
            response = minio_client.get_object(settings.minio.bucket, file_path)
            file_content = response.read()
            response.close()
            response.release_conn()
        except Exception:
            logger.exception("MinIO 拉取失败")
            await publish_ingest_result(doc_id, "FAILED", {"error": "MinIO download failed"})
            return

        st = KbSourceType(source_type)
        if st in (KbSourceType.MARKDOWN, KbSourceType.HTML, KbSourceType.TXT):
            file_path_or_content = file_content.decode("utf-8")
        else:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{doc_id}")
            tmp.write(file_content)
            tmp.close()
            file_path_or_content = tmp.name

        try:
            session_factory = get_session_factory()
            es_client = get_es()

            async with session_factory() as session:
                status = await ingest_document(
                    doc_id=doc_id,
                    file_path=file_path_or_content,
                    source_type=st,
                    metadata=metadata,
                    embedding_provider=embedding_breaker.provider,
                    db_session=session,
                    es_client=es_client,
                    llm_extractor=llm_extractor,
                )
                await session.commit()

            await publish_ingest_result(doc_id, status, {"file_path": file_path})
            logger.info("文档处理完成", status=status)
            await clear_retry(doc_id)

        except Exception as e:
            retry_count = await increment_retry(doc_id)
            max_retries = settings.kafka.max_retries

            if retry_count >= max_retries:
                logger.exception("文档处理失败，超过最大重试次数，投递 DLQ",
                                 retry_count=retry_count, max_retries=max_retries)
                await publish_to_dlq(doc_id, message, str(e))
                await clear_retry(doc_id)
            else:
                logger.exception("文档处理失败，将重试",
                                 retry_count=retry_count, max_retries=max_retries)
                raise  # 重新抛出，由上层 seek 回退

        finally:
            if st not in (KbSourceType.MARKDOWN, KbSourceType.HTML, KbSourceType.TXT):
                try:
                    os.unlink(file_path_or_content)
                except OSError:
                    pass

    finally:
        # 释放并发锁
        if lock_token:
            await release_lock(f"ingest:{doc_id}", lock_token)
        clear_context()


async def run_worker() -> None:
    """启动 Kafka Worker 主循环"""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("启动 Knowledge Platform Worker...")

    # 初始化基础设施
    await init_es()
    init_minio()
    await init_producer()

    # 初始化嵌入服务
    ollama_base = settings.llm.base_url.replace("/v1", "").rstrip("/")
    provider = create_embedding_provider(
        provider_type=settings.rag.embedding_provider,
        ollama_base_url=ollama_base,
        ollama_model=settings.rag.embedding_model,
        tei_base_url=settings.rag.tei_base_url,
        tei_model=settings.rag.embedding_model,
        dim=settings.rag.embedding_dim,
        batch_size=settings.rag.embedding_batch_size,
        timeout=settings.rag.embedding_timeout,
    )
    breaker = EmbeddingCircuitBreaker(provider)
    await breaker.start_probe()

    # LLM 抽取器
    extractor = LLMExtractor()

    # 信号处理：优雅停机
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutting_down.set)

    # 创建消费者
    consumer = create_consumer()
    await consumer.start()
    logger.info("Worker 已启动，等待消息...")

    try:
        # 使用 getone() 显式控制拉取，避免 async for + seek 缓冲不一致
        while not _shutting_down.is_set():
            try:
                msg = await asyncio.wait_for(consumer.getone(), timeout=5.0)
            except asyncio.TimeoutError:
                # 超时无消息，循环检查停机标志
                continue

            msg_doc_id = msg.value.get("doc_id", "unknown") if isinstance(msg.value, dict) else "unknown"
            try:
                await process_message(msg.value, breaker, extractor)
                await consumer.commit()
            except Exception:
                # process_message 重新抛出 = 重试未耗尽
                retry_count = await get_retry_count(msg_doc_id)
                if retry_count == 0:
                    # 计数被清除 = 已投递 DLQ
                    logger.warning("消息已投递 DLQ，提交 offset", doc_id=msg_doc_id, offset=msg.offset)
                    await consumer.commit()
                else:
                    # 未耗尽重试：seek 回退到当前消息 offset，不 commit
                    # getone() 下次会重新拉取 seek 位置的同一消息
                    from aiokafka import TopicPartition

                    tp = TopicPartition(msg.topic, msg.partition)
                    await consumer.seek(tp, msg.offset)
                    logger.warning("消息处理失败，seek 回退等待重试",
                                   doc_id=msg_doc_id, retry_count=retry_count, offset=msg.offset)
                    await asyncio.sleep(min(retry_count * 2, 10))  # 退避等待

    finally:
        # 优雅停机：等待 in-flight 消息处理完成
        logger.info("正在关闭 Worker（等待 in-flight 完成）...")
        await consumer.stop()
        await breaker.stop_probe()
        await close_producer()
        await close_es()
        await close_engine()
        logger.info("Worker 已关闭")


def main() -> None:
    """Worker 入口点"""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
