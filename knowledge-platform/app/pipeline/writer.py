"""写入器 — PG + ES 单写

架构简化：不再双写 ES+Milvus。
- PG：唯一真相源，chunk 正文 + embedding 向量 + model_version
- ES：派生索引（BM25+IK + kNN dense_vector），可从 PG 重建
- ES 写入失败只需 re-index 任务，无需 Saga 补偿
"""

from __future__ import annotations

import logging
import struct
from datetime import date
from typing import Any

from elasticsearch import AsyncElasticsearch
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.orm.kb import KbChunk, KbEmbedStatus

logger = logging.getLogger(__name__)


def serialize_embedding(embedding: list[float]) -> bytes:
    """将 float32 向量序列化为 bytes（PG LargeBinary 存储）"""
    return struct.pack(f"{len(embedding)}f", *embedding)


def deserialize_embedding(data: bytes) -> list[float]:
    """从 bytes 反序列化为 float32 向量列表"""
    count = len(data) // 4
    return list(struct.unpack(f"{count}f", data))


def _date_to_epoch(d: date | str | None) -> int:
    """date 或 ISO 字符串转 epoch 秒（与 ES mapping epoch_second 对齐）

    Kafka 消息中 effective_date 是 ISO 字符串（如 "2024-01-01"），
    直接调用时可能是 date 对象，需兼容两种输入。
    """
    if d is None:
        return 0
    from datetime import datetime

    if isinstance(d, str):
        try:
            dt = datetime.fromisoformat(d)
        except ValueError:
            return 0
    elif isinstance(d, date):
        dt = datetime(d.year, d.month, d.day)
    else:
        return 0
    return int(dt.timestamp())


async def write_chunks_to_pg(
    session: AsyncSession,
    doc_id: Any,
    chunks: list[dict],
    embeddings: list[list[float]],
    model_version: str,
) -> list[str]:
    """将分块 + 嵌入向量写入 PG

    Args:
        session: 数据库会话
        doc_id: 文档 ID
        chunks: 分块数据列表（content, chunk_type, heading_path, parent_index 等）
        embeddings: 嵌入向量列表，与 chunks 一一对应
        model_version: 嵌入模型版本标识

    Returns:
        chunk_id 列表
    """
    import uuid_utils

    chunk_ids: list[str] = []
    chunk_id_map: dict[int, str] = {}

    for idx, (chunk_data, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
        chunk_id = str(uuid_utils.uuid7())
        chunk_id_map[idx] = chunk_id

    for idx, (chunk_data, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
        chunk_id = chunk_id_map[idx]
        parent_idx = chunk_data.get("parent_index")
        parent_chunk_id = chunk_id_map.get(parent_idx) if parent_idx is not None else None

        chunk = KbChunk(
            id=chunk_id,
            document_id=doc_id,
            chunk_index=idx,
            content=chunk_data["content"],
            char_count=len(chunk_data["content"]),
            embedding_status=KbEmbedStatus.COMPLETED,
            es_indexed=False,
            embedding=serialize_embedding(embedding),
            model_version=model_version,
            parent_chunk_id=parent_chunk_id,
            chunk_type=chunk_data.get("chunk_type", "plain_text"),
            heading_path=" > ".join(chunk_data.get("heading_path", [])) or None,
        )
        session.add(chunk)
        chunk_ids.append(chunk_id)

    await session.flush()
    logger.info("PG 写入完成: doc_id=%s, chunks=%d", doc_id, len(chunk_ids))
    return chunk_ids


async def write_chunks_to_es(
    es_client: AsyncElasticsearch | None,
    doc_id: str,
    chunk_ids: list[str],
    chunks: list[dict],
    embeddings: list[list[float]],
    doc_metadata: dict[str, Any],
    model_version: str,
) -> int:
    """将分块写入 ES（BM25 文本 + kNN dense_vector）

    Args:
        es_client: ES 异步客户端
        doc_id: 文档 ID
        chunk_ids: chunk ID 列表
        chunks: 分块数据
        embeddings: 嵌入向量
        doc_metadata: 文档元数据（category, doc_type, keywords 等）
        model_version: 嵌入模型版本

    Returns:
        成功写入数
    """
    if es_client is None:
        logger.warning("ES 客户端未初始化，跳过 ES 写入")
        return 0

    settings = get_settings()
    index_name = settings.elasticsearch.chunks_index
    success = 0

    for chunk_id, chunk_data, embedding in zip(chunk_ids, chunks, embeddings, strict=True):
        try:
            await es_client.index(
                index=index_name,
                id=chunk_id,
                document={
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "content": chunk_data["content"],
                    "embedding": embedding,
                    "model_version": model_version,
                    "category": doc_metadata.get("category", ""),
                    "doc_type": doc_metadata.get("doc_type", ""),
                    "keywords": doc_metadata.get("keywords", []),
                    "card_type": doc_metadata.get("card_type", ""),
                    "customer_tier": doc_metadata.get("customer_tier", ""),
                    "security_level": doc_metadata.get("security_level", "internal"),
                    "version": doc_metadata.get("version", "1.0"),
                    "chunk_type": chunk_data.get("chunk_type", "plain_text"),
                    "heading_path": " > ".join(chunk_data.get("heading_path", [])),
                    "approval_status": doc_metadata.get("approval_status", "PUBLISHED"),
                    "is_current_version": doc_metadata.get("is_current_version", True),
                    "doc_group": doc_metadata.get("doc_group", doc_id),
                    "effective_date": _date_to_epoch(doc_metadata.get("effective_date")),
                    "expiry_date": _date_to_epoch(doc_metadata.get("expiry_date")),
                },
            )
            success += 1
        except Exception:
            logger.exception("ES 写入失败: chunk_id=%s", chunk_id)

    logger.info("ES 写入完成: doc_id=%s, success=%d/%d", doc_id, success, len(chunk_ids))
    return success


async def mark_es_indexed(
    session: AsyncSession,
    chunk_ids: list[str],
) -> None:
    """标记 chunk 在 ES 中已索引"""
    from sqlalchemy import update

    await session.execute(
        update(KbChunk)
        .where(KbChunk.id.in_(chunk_ids))
        .values(es_indexed=True)
    )
    await session.flush()


async def delete_chunks_from_es(
    es_client: AsyncElasticsearch | None,
    doc_id: str,
) -> int:
    """从 ES 删除文档的所有 chunk（增量索引：文档修订时先删旧再写新）"""
    if es_client is None:
        return 0

    settings = get_settings()
    index_name = settings.elasticsearch.chunks_index

    try:
        body = {"query": {"term": {"doc_id": doc_id}}}
        resp = await es_client.delete_by_query(index=index_name, body=body, refresh=True)
        deleted = resp.get("deleted", 0)
        logger.info("ES 删除完成: doc_id=%s, deleted=%d", doc_id, deleted)
        return deleted
    except Exception:
        logger.exception("ES 删除失败: doc_id=%s", doc_id)
        return 0
