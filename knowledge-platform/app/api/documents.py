"""文档管理 API

- POST /documents: 上传文档 → 校验 → 敏感词扫描 → MinIO → PG → Kafka 异步任务
- GET /documents/{id}: 查询文档状态
- GET /documents: 文档列表（分页）
- POST /documents/{id}/reindex: 重建 ES 索引

安全措施：
- API Key 认证
- 文件类型白名单 + 大小限制
- 文件名安全化（防路径穿越）
- 敏感词 AC 自动机扫描
- 并发 ETL 分布式锁（Redis SETNX）
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import uuid_utils
from datetime import date

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select

from app.api.deps import ApiKeyDep, DbSession, ESClient
from app.config import get_settings
from app.orm.kb import KbDocStatus, KbDocument, KbSourceType
from app.pipeline.parser import detect_source_type
from app.security.sensitive_filter import get_sensitive_filter
from app.storage.kafka import publish_ingest_request
from app.storage.minio import get_minio
from app.storage.redis import acquire_lock, release_lock
from app.utils import sanitize_filename

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

# 允许的文件扩展名
_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".html", ".htm", ".md", ".markdown", ".txt", ".xlsx"}


@router.post("", status_code=202)
async def upload_document(
    db: DbSession,
    _api_key: ApiKeyDep,
    file: UploadFile = File(...),
    category: str = Form("OTHER"),
    doc_type: str = Form("faq"),
    card_type: str | None = Form(None),
    customer_tier: str | None = Form(None),
    security_level: str = Form("internal"),
    version: str = Form("1.0"),
    effective_date: str | None = Form(None),
    expiry_date: str | None = Form(None),
    keywords: str | None = Form(None),
):
    """上传文档

    流程：校验 → 敏感词扫描 → 上传 MinIO → 建 KbDocument → 投递 Kafka → 202
    """
    settings = get_settings()

    # ── 1. 文件大小校验 ──
    content = await file.read()
    max_size = settings.security.max_upload_size_mb * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过大小限制 {settings.security.max_upload_size_mb}MB",
        )
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")

    # ── 2. 文件类型白名单校验 ──
    filename = file.filename or "unknown.txt"
    ext = os.path.splitext(filename)[1].lower()
    allowed = settings.allowed_extensions_list or _ALLOWED_EXTENSIONS
    if ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型: {ext}，允许: {', '.join(sorted(allowed))}",
        )

    # ── 3. 文件名安全化 ──
    safe_filename = sanitize_filename(filename)

    # ── 4. 敏感词预扫描（仅文本文件，二进制文件在 ETL Parse 后扫描） ──
    source_type = detect_source_type(safe_filename)
    if source_type in (KbSourceType.MARKDOWN, KbSourceType.HTML, KbSourceType.TXT):
        try:
            text_content = content.decode("utf-8", errors="ignore")
            sensitive_filter = get_sensitive_filter()
            hits = sensitive_filter.scan(text_content)
            if hits:
                hit_words = [h["word"] for h in hits[:10]]
                logger.warning("文档包含敏感词，拒绝上传", filename=safe_filename, hits=hit_words)
                raise HTTPException(
                    status_code=422,
                    detail=f"文档包含敏感词: {', '.join(hit_words)}",
                )
        except HTTPException:
            raise
        except Exception:
            logger.exception("敏感词预扫描异常", filename=safe_filename)

    # ── 5. MinIO 上传 ──
    object_key = f"{category}/{safe_filename}"
    minio_client = get_minio()
    if minio_client is None:
        raise HTTPException(status_code=503, detail="MinIO 不可用")

    minio_client.put_object(
        settings.minio.bucket,
        object_key,
        io.BytesIO(content),
        length=len(content),
        content_type=file.content_type or "application/octet-stream",
    )

    # ── 7. 创建文档记录 ──
    content_hash = hashlib.sha256(content).hexdigest()
    eff_date = _parse_date(effective_date)
    exp_date = _parse_date(expiry_date)
    kw_list = [k.strip() for k in (keywords or "").split(",") if k.strip()] if keywords else []

    doc_id = uuid_utils.uuid7()
    doc = KbDocument(
        id=doc_id,
        title=safe_filename,
        source_type=source_type,
        file_path=object_key,
        file_size=len(content),
        content_hash=content_hash,
        category=category,
        doc_type=doc_type,
        card_type=card_type,
        customer_tier=customer_tier,
        security_level=security_level,
        version=version,
        effective_date=eff_date,
        expiry_date=exp_date,
        status=KbDocStatus.PENDING,
        is_deleted=False,
        created_by="api",
    )
    db.add(doc)
    await db.commit()

    # ── 8. 投递 Kafka 异步任务 ──
    payload = {
        "doc_id": str(doc_id),
        "file_path": object_key,
        "source_type": source_type.value,
        "metadata": {
            "title": doc.title,
            "category": category,
            "doc_type": doc_type,
            "card_type": card_type or "",
            "customer_tier": customer_tier or "",
            "security_level": security_level,
            "version": version,
            "effective_date": eff_date.isoformat() if eff_date else None,
            "expiry_date": exp_date.isoformat() if exp_date else None,
            "keywords": kw_list,
            "approval_status": "PUBLISHED",
            "is_current_version": True,
            "doc_group": str(doc_id),
        },
    }

    try:
        await publish_ingest_request(str(doc_id), payload)
        doc.status = KbDocStatus.KAFKA_QUEUED
        await db.commit()
    except Exception as e:
        logger.warning("Kafka 投递失败", doc_id=str(doc_id), error=str(e))
        raise HTTPException(status_code=503, detail=f"任务投递失败: {e}")

    return {"doc_id": str(doc_id), "status": "KAFKA_QUEUED", "message": "文档已上传，ETL 任务已投递"}


@router.get("/{doc_id}")
async def get_document(doc_id: str, db: DbSession, _api_key: ApiKeyDep):
    """查询文档状态"""
    try:
        uid = uuid_utils.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 doc_id")

    doc = await db.get(KbDocument, uid)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    return {
        "doc_id": str(doc.id),
        "title": doc.title,
        "status": doc.status.value,
        "category": doc.category,
        "doc_type": doc.doc_type,
        "chunk_count": doc.chunk_count,
        "approval_status": doc.approval_status.value,
        "llm_summary": doc.llm_summary,
        "llm_keywords": doc.llm_keywords,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


@router.get("")
async def list_documents(
    db: DbSession,
    _api_key: ApiKeyDep,
    category: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """文档列表（分页）"""
    limit = min(limit, 200)  # 上限保护
    offset = max(offset, 0)

    query = select(KbDocument).where(KbDocument.is_deleted.is_(False))
    if category:
        query = query.where(KbDocument.category == category)
    if status:
        query = query.where(KbDocument.status == status)
    query = query.order_by(KbDocument.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    docs = result.scalars().all()

    # 总数（用于分页）
    count_query = select(func.count()).select_from(KbDocument).where(KbDocument.is_deleted.is_(False))
    if category:
        count_query = count_query.where(KbDocument.category == category)
    if status:
        count_query = count_query.where(KbDocument.status == status)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "documents": [
            {
                "doc_id": str(d.id),
                "title": d.title,
                "category": d.category,
                "status": d.status.value,
                "chunk_count": d.chunk_count,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ],
    }


@router.post("/{doc_id}/reindex")
async def reindex_document(doc_id: str, db: DbSession, es: ESClient, _api_key: ApiKeyDep):
    """重建 ES 索引（从 PG 读取 chunk 重灌 ES，不需重跑嵌入模型）"""
    from app.orm.kb import KbChunk
    from app.pipeline.writer import (
        deserialize_embedding,
        delete_chunks_from_es,
        mark_es_indexed,
        write_chunks_to_es,
    )

    try:
        uid = uuid_utils.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 doc_id")

    if es is None:
        raise HTTPException(status_code=503, detail="ES 不可用")

    await delete_chunks_from_es(es, doc_id)

    result = await db.execute(
        select(KbChunk).where(KbChunk.document_id == uid).order_by(KbChunk.chunk_index)
    )
    chunks = result.scalars().all()
    if not chunks:
        raise HTTPException(status_code=404, detail="文档无分块数据")

    doc = await db.get(KbDocument, uid)
    metadata = {
        "category": doc.category,
        "doc_type": doc.doc_type,
        "card_type": doc.card_type or "",
        "customer_tier": doc.customer_tier or "",
        "security_level": doc.security_level,
        "version": doc.version,
        "keywords": doc.llm_keywords or [],
        "approval_status": doc.approval_status.value,
        "is_current_version": doc.is_current_version,
        "doc_group": doc.doc_group or doc_id,
    }

    chunk_ids = []
    chunks_data = []
    embeddings = []
    for c in chunks:
        chunk_ids.append(str(c.id))
        chunks_data.append({
            "content": c.content,
            "chunk_type": c.chunk_type,
            "heading_path": c.heading_path.split(" > ") if c.heading_path else [],
        })
        embeddings.append(deserialize_embedding(c.embedding) if c.embedding else [])

    success = await write_chunks_to_es(
        es, doc_id, chunk_ids, chunks_data, embeddings,
        metadata, chunks[0].model_version or "unknown",
    )

    await mark_es_indexed(db, chunk_ids)
    await db.commit()

    return {"doc_id": doc_id, "reindexed": success, "total": len(chunks)}


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None
