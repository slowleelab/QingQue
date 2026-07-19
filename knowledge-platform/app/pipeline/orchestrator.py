"""ETL 管线编排器

7 阶段流水线：Parse → Clean → EXTRACT → Chunk → Embed → ES_Write → KAFKA_PUBLISH
Worker 消费 Kafka 任务后调用此编排器执行完整 ETL。

关键改进（相比 SmartCS ingestion.py）：
1. 新增 EXTRACT 阶段 — LLM 自动抽取关键词/摘要/实体
2. 删除 Milvus 双写 — 只写 PG(真相源) + ES(派生索引)
3. 删除 Saga 回滚 — ES 失败走 re-index 任务
4. 增量索引 — 文档修订时先删旧 chunk 再写新 chunk
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging import get_logger
from app.orm.kb import (
    KbChunk,
    KbDocStatus,
    KbDocument,
    KbIngestionLog,
    KbIngestionStage,
    KbIngestionStatus,
    KbSourceType,
)
from app.pipeline.chunker import chunk_by_structure
from app.pipeline.cleaner import clean_text
from app.pipeline.embedder import EmbeddingProvider, embed_chunks
from app.pipeline.extractor import LLMExtractor
from app.pipeline.parser import parse
from app.pipeline.writer import (
    delete_chunks_from_es,
    write_chunks_to_es,
    write_chunks_to_pg,
)

logger = get_logger(__name__)


async def _log_stage(
    session: AsyncSession,
    doc_id: Any,
    stage: KbIngestionStage,
    status: KbIngestionStatus,
    duration_ms: int,
    step_detail: dict | None = None,
    error_message: str | None = None,
) -> None:
    """记录摄入流水日志"""
    log = KbIngestionLog(
        document_id=doc_id,
        stage=stage,
        status=status,
        duration_ms=duration_ms,
        step_detail=step_detail,
        error_message=error_message,
    )
    session.add(log)
    await session.flush()


async def ingest_document(
    doc_id: Any,
    file_path: str,
    source_type: KbSourceType | str,
    metadata: dict[str, Any],
    embedding_provider: EmbeddingProvider,
    db_session: AsyncSession,
    es_client: Any | None = None,
    llm_extractor: LLMExtractor | None = None,
    chunk_size: int = 1500,
    chunk_overlap: int = 200,
) -> str:
    """文档摄入编排器

    7 阶段：Parse → Clean → Extract → Chunk → Embed → ES_Write

    Returns:
        最终状态: COMPLETED / FAILED
    """
    settings = get_settings()
    model_version = settings.rag.embedding_model_version

    doc = await db_session.get(KbDocument, doc_id)
    if doc is None:
        raise ValueError(f"文档不存在: {doc_id}")

    doc.status = KbDocStatus.PROCESSING
    await db_session.flush()

    try:
        # ── 0. 增量索引：删除文档的旧 chunk（文档修订场景） ──
        existing_chunks = await db_session.execute(
            select(KbChunk.id).where(KbChunk.document_id == doc_id)
        )
        old_chunk_ids = [str(row[0]) for row in existing_chunks]
        if old_chunk_ids:
            logger.info("增量索引：删除旧 chunk", doc_id=str(doc_id), old_count=len(old_chunk_ids))
            await db_session.execute(
                delete(KbChunk).where(KbChunk.document_id == doc_id)
            )
            await db_session.flush()
            # 同步删除 ES 中的旧 chunk
            if es_client is not None:
                await delete_chunks_from_es(es_client, str(doc_id))

        # ── 1. Parse ──
        t0 = time.perf_counter()
        raw_text = parse(source_type, file_path)
        await _log_stage(
            db_session, doc_id, KbIngestionStage.PARSE,
            KbIngestionStatus.SUCCESS, int((time.perf_counter() - t0) * 1000),
            step_detail={"text_length": len(raw_text)},
        )

        # ── 1.5 敏感词扫描（对解析后的真实文本，覆盖所有文件类型） ──
        # 上传 API 只对文本文件(MD/TXT/HTML)做了预扫描，
        # 这里对 Parse 后的纯文本做二次扫描，覆盖 PDF/DOCX/XLSX 等二进制文件。
        # 命中则标记 FAILED，不继续后续阶段（文件已在 MinIO，但不会被索引）。
        try:
            from app.security.sensitive_filter import get_sensitive_filter

            sf = get_sensitive_filter()
            hits = sf.scan(raw_text)
            if hits:
                hit_words = [h["word"] for h in hits[:20]]
                logger.warning("文档包含敏感词，终止 ETL", doc_id=str(doc_id), hits=hit_words)
                doc.status = KbDocStatus.FAILED
                await db_session.flush()
                await _log_stage(
                    db_session, doc_id, KbIngestionStage.PARSE,
                    KbIngestionStatus.FAILED, 0,
                    error_message=f"敏感词命中: {', '.join(hit_words)}",
                    step_detail={"sensitive_words": hit_words},
                )
                return "FAILED"
        except Exception:
            logger.exception("敏感词扫描异常，继续管线", doc_id=str(doc_id))

        # ── 2. Clean ──
        t0 = time.perf_counter()
        cleaned = clean_text(raw_text)
        await _log_stage(
            db_session, doc_id, KbIngestionStage.CLEAN,
            KbIngestionStatus.SUCCESS, int((time.perf_counter() - t0) * 1000),
            step_detail={"cleaned_length": len(cleaned)},
        )

        # ── 3. Extract (LLM) ──
        if llm_extractor is not None:
            t0 = time.perf_counter()
            try:
                extraction_result = await llm_extractor.extract(
                    cleaned,
                    doc_type=metadata.get("doc_type", ""),
                    title=metadata.get("title", ""),
                )
                doc.llm_summary = extraction_result.summary
                doc.llm_keywords = extraction_result.keywords
                doc.llm_entities = extraction_result.entities
                await db_session.flush()

                if extraction_result.keywords:
                    existing = metadata.get("keywords", [])
                    if isinstance(existing, list):
                        merged = list(set(existing + extraction_result.keywords))
                    else:
                        merged = extraction_result.keywords
                    metadata["keywords"] = merged

                await _log_stage(
                    db_session, doc_id, KbIngestionStage.EXTRACT,
                    KbIngestionStatus.SUCCESS, int((time.perf_counter() - t0) * 1000),
                    step_detail={
                        "keywords": len(extraction_result.keywords),
                        "entities": len(extraction_result.entities),
                        "faq_pairs": len(extraction_result.faq_pairs),
                    },
                )
            except Exception as e:
                await _log_stage(
                    db_session, doc_id, KbIngestionStage.EXTRACT,
                    KbIngestionStatus.FAILED, int((time.perf_counter() - t0) * 1000),
                    error_message=str(e),
                )
                # 抽取失败不阻塞管线

        # ── 4. Chunk ──
        t0 = time.perf_counter()
        st = KbSourceType(source_type) if isinstance(source_type, str) else source_type
        structured_chunks = chunk_by_structure(
            cleaned,
            source_type=st.value,
            doc_metadata=metadata,
            max_chunk_size=chunk_size,
            overlap=chunk_overlap,
            doc_type=metadata.get("doc_type", ""),
        )
        chunks_data = [
            {
                "content": c.content,
                "chunk_type": c.chunk_type.value,
                "heading_path": c.heading_path,
                "parent_index": c.parent_index,
            }
            for c in structured_chunks
        ]
        await _log_stage(
            db_session, doc_id, KbIngestionStage.CHUNK,
            KbIngestionStatus.SUCCESS, int((time.perf_counter() - t0) * 1000),
            step_detail={"chunk_count": len(chunks_data)},
        )

        if not chunks_data:
            doc.status = KbDocStatus.FAILED
            await db_session.flush()
            return "FAILED"

        # ── 5. Embed ──
        t0 = time.perf_counter()
        chunk_texts = [c["content"] for c in chunks_data]
        embeddings = await embed_chunks(chunk_texts, embedding_provider)
        await _log_stage(
            db_session, doc_id, KbIngestionStage.EMBED,
            KbIngestionStatus.SUCCESS, int((time.perf_counter() - t0) * 1000),
            step_detail={"dim": len(embeddings[0]) if embeddings else 0, "model_version": model_version},
        )

        # ── 6. PG 写入（真相源） ──
        chunk_ids = await write_chunks_to_pg(
            db_session, doc_id, chunks_data, embeddings, model_version,
        )

        # ── 7. ES 写入（派生索引，失败走 re-index） ──
        if es_client is not None:
            t0 = time.perf_counter()
            es_success = await write_chunks_to_es(
                es_client, str(doc_id), chunk_ids, chunks_data,
                embeddings, metadata, model_version,
            )
            await _log_stage(
                db_session, doc_id, KbIngestionStage.ES_WRITE,
                KbIngestionStatus.SUCCESS if es_success == len(chunk_ids) else KbIngestionStatus.FAILED,
                int((time.perf_counter() - t0) * 1000),
                step_detail={"success": es_success, "total": len(chunk_ids)},
            )

        doc.status = KbDocStatus.COMPLETED
        doc.chunk_count = len(chunks_data)
        await db_session.flush()

        # ── 8. 记录 KAFKA_PUBLISH 阶段（Worker 发布结果事件） ──
        # 实际的 Kafka publish 在 Worker 中执行，这里只记录流水日志
        await _log_stage(
            db_session, doc_id, KbIngestionStage.KAFKA_PUBLISH,
            KbIngestionStatus.SUCCESS, 0,
            step_detail={"model_version": model_version, "chunk_count": len(chunks_data)},
        )

        logger.info("文档摄入完成", doc_id=str(doc_id), chunks=len(chunks_data), model_version=model_version)
        return "COMPLETED"

    except Exception:
        logger.exception("文档摄入失败", doc_id=str(doc_id))
        doc.status = KbDocStatus.FAILED
        await db_session.flush()
        return "FAILED"
