"""文档管理 API

- POST /documents: 上传文档 → 校验 → 敏感词扫描 → MinIO → PG → Kafka 异步任务
- GET /documents/{id}: 查询文档状态
- GET /documents: 文档列表（分页）
- POST /documents/{id}/reindex: 重建 ES 索引
- GET /documents/{id}/versions: 同 doc_group 下的版本列表 (I2-C3)
- POST /documents/{id}/rollback: 切换到指定历史版本 (I2-C3, 原子操作 + 审计)
- GET /documents/{id}/diff: 两版本 diff (I2-C3, 字段级对比)
- POST /documents/{id}/takedown: 紧急下架 (I2-C3, 独立于 archive, 强留痕)

安全措施：
- API Key 认证
- 文件类型白名单 + 大小限制
- 文件名安全化（防路径穿越）
- 敏感词 AC 自动机扫描
- 并发 ETL 分布式锁（Redis SETNX）
- 版本切换/下架/回滚均写 KbDocumentApproval + AuditService
- P0-1: allowed_roles 注入 (多租户隔离, 角色访问控制)
"""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import logging
import os
import uuid
import uuid_utils
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from kb.api.deps import DbSession, ESClient, PrincipalDep
from kb.config import get_settings
from kb.logging import get_logger
from kb.orm.kb import (
    KbApprovalAction,
    KbApprovalStatus,
    KbDocStatus,
    KbDocument,
    KbDocumentApproval,
    KbSourceType,
)
from kb.pipeline.parser import detect_source_type
from kb.security.audit_service import AuditService, extract_request_meta
from kb.security.sensitive_filter import get_sensitive_filter
from kb.storage.kafka import publish_ingest_request
from kb.storage.minio import get_minio
from kb.storage.redis import acquire_lock, release_lock
from kb.utils import sanitize_filename

logger = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

# 允许的文件扩展名
_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".html", ".htm", ".md", ".markdown", ".txt", ".xlsx"}


def _build_reindex_metadata(doc: Any, doc_id_fallback: str | None = None) -> dict[str, Any]:
    """P0-2.3: 构造 reindex / publish 同步用的 ES metadata

    单一构造点, 防 reindex_document / publish es_sync / upload 三个路径
    各自 inline metadata dict 出现漂移.

    字段:
      - category / doc_type / card_type / customer_tier / security_level: 业务属性
      - version: doc.version
      - tenant_id: doc.tenant_id (P0-1)
      - keywords: doc.llm_keywords (LLM 抽取)
      - approval_status: doc.approval_status.value (合规过滤, 必须跟 PG 一致)
      - is_current_version: doc.is_current_version (版本管理)
      - doc_group: doc.doc_group or doc_id_fallback (I2-C3)
      - allowed_roles: doc.allowed_roles (P0-1 角色访问控制)
    """
    return {
        "category": doc.category,
        "doc_type": doc.doc_type,
        "card_type": doc.card_type or "",
        "customer_tier": doc.customer_tier or "",
        "security_level": doc.security_level,
        "version": doc.version,
        "tenant_id": doc.tenant_id,
        "keywords": doc.llm_keywords or [],
        "approval_status": doc.approval_status.value if hasattr(doc.approval_status, "value") else str(doc.approval_status),
        "is_current_version": doc.is_current_version,
        "doc_group": doc.doc_group or doc_id_fallback or str(doc.id),
        "allowed_roles": doc.allowed_roles or [],
    }


def _parse_allowed_roles(raw: str | None) -> list[str]:
    """P0-1: 解析上传表单里的 allowed_roles 字段

    接收 JSON 字符串, 例如 '["admin","editor"]'.
    - 缺省/空字符串 → []  (空列表 = 全员可见, Confluence/SharePoint 默认)
    - 非 JSON → 422
    - JSON 但非 list → 422
    - 元素必须非空字符串
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=422,
            detail=f"allowed_roles 必须是合法 JSON 数组: {e.msg}",
        ) from e
    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=422,
            detail=f"allowed_roles 必须是 JSON 数组, 当前类型: {type(parsed).__name__}",
        )
    cleaned: list[str] = []
    for item in parsed:
        if not isinstance(item, str) or not item.strip():
            raise HTTPException(
                status_code=422,
                detail="allowed_roles 元素必须是非空字符串",
            )
        cleaned.append(item.strip())
    return cleaned


@router.post("", status_code=202)
async def upload_document(
    db: DbSession,
    principal: PrincipalDep,
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
    allowed_roles: str | None = Form(None),  # P0-1: JSON 数组字符串
):
    """上传文档

    流程：校验 → 敏感词扫描 → 上传 MinIO → 建 KbDocument → 投递 Kafka → 202
    """
    settings = get_settings()

    # P0-1: 解析 allowed_roles (必须在文件校验前, 避免无效请求占用 IO)
    parsed_allowed_roles = _parse_allowed_roles(allowed_roles)

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
        tenant_id=principal.tenant_id,  # I1-C1: 多租户从 JWT 注入
        allowed_roles=parsed_allowed_roles,  # P0-1: 角色访问控制 (空 = 全员可见)
        created_by=principal.actor_id,  # I1-C3: 双签需要真实 created_by
    )
    db.add(doc)
    await db.commit()

    # ── 8. 投递 Kafka 异步任务 ──
    payload = {
        "doc_id": str(doc_id),
        "file_path": object_key,
        "source_type": source_type.value,
        "tenant_id": principal.tenant_id,  # I1-C1: 多租户透传到 ETL
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
            "approval_status": "DRAFT",
            "is_current_version": True,
            "doc_group": str(doc_id),
            # P0-1: 多租户 + 角色访问控制透传到 ETL, 写 ES 用
            "allowed_roles": parsed_allowed_roles,
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
async def get_document(doc_id: str, db: DbSession, principal: PrincipalDep):
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
    principal: PrincipalDep,
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
async def reindex_document(doc_id: str, db: DbSession, es: ESClient, principal: PrincipalDep):
    """重建 ES 索引（从 PG 读取 chunk 重灌 ES，不需重跑嵌入模型）"""
    from kb.orm.kb import KbChunk
    from kb.pipeline.writer import (
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
    # P0-2.3: 复用 _build_reindex_metadata, 避免 inline 漂移
    metadata = _build_reindex_metadata(doc, doc_id_fallback=doc_id)

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


# ──────────────────────────────────────────────────────────────────────
# I2-C3: 版本管理 — 列表 / 回滚 / diff / 紧急下架
# ──────────────────────────────────────────────────────────────────────


class VersionInfo(BaseModel):
    doc_id: str
    version: str
    is_current: bool
    approval_status: str
    created_at: str
    created_by: str | None
    chunk_count: int | None
    content_hash: str | None


class VersionListResponse(BaseModel):
    doc_group: str
    current_doc_id: str
    versions: list[VersionInfo]


class RollbackRequest(BaseModel):
    target_doc_id: str = Field(..., description="要切换到的历史版本 doc_id")
    comment: str = Field(..., min_length=1, max_length=2000, description="回滚原因 (合规留痕, 必填)")


class RollbackResponse(BaseModel):
    from_doc_id: str
    to_doc_id: str
    from_version: str
    to_version: str
    approval_id: str
    actor_id: str
    created_at: str


class DiffField(BaseModel):
    field: str
    from_value: str | None
    to_value: str | None
    changed: bool


class DiffResponse(BaseModel):
    from_doc_id: str
    to_doc_id: str
    fields: list[DiffField]
    content_unified_diff: str | None = Field(
        default=None,
        description="content_hash 层面的 unified diff 占位 (实际对比走 chunk 级)",
    )


class TakedownRequest(BaseModel):
    """紧急下架请求 — 高风险, 必填 comment"""

    comment: str = Field(..., min_length=5, max_length=2000, description="下架原因 (至少 5 字符)")
    reason: str = Field(default="other", description="原因分类: regulatory/security/quality/other")


class TakedownResponse(BaseModel):
    doc_id: str
    approval_id: str
    actor_id: str
    comment: str
    reason: str
    created_at: str


@router.get("/{doc_id}/versions", response_model=VersionListResponse)
async def list_versions(
    doc_id: str,
    db: DbSession,
    principal: PrincipalDep,
):
    """列出同 doc_group 下的所有版本 (含当前/历史)

    按 is_current 优先, 然后 created_at 倒序.
    """
    try:
        uid = uuid_utils.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 doc_id")

    doc = await db.get(KbDocument, uid)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.is_deleted:
        raise HTTPException(status_code=410, detail="文档已删除")

    doc_group = doc.doc_group or str(doc.id)

    result = await db.execute(
        select(KbDocument)
        .where(KbDocument.doc_group == doc_group)
        .order_by(KbDocument.is_current_version.desc(), KbDocument.created_at.desc())
    )
    docs = result.scalars().all()

    # 找当前生效版本
    current_id = str(doc.id) if doc.is_current_version else None
    if not current_id:
        for d in docs:
            if d.is_current_version:
                current_id = str(d.id)
                break

    return VersionListResponse(
        doc_group=doc_group,
        current_doc_id=current_id or str(doc.id),
        versions=[
            VersionInfo(
                doc_id=str(d.id),
                version=d.version,
                is_current=d.is_current_version,
                approval_status=d.approval_status.value,
                created_at=d.created_at.isoformat() if d.created_at else "",
                created_by=d.created_by,
                chunk_count=d.chunk_count,
                content_hash=d.content_hash,
            )
            for d in docs
        ],
    )


@router.post("/{doc_id}/rollback", response_model=RollbackResponse)
async def rollback_document(
    doc_id: str,
    payload: RollbackRequest,
    request: Request,  # P0-3.A: 透传 IP/UA/request_id
    db: DbSession,
    principal: PrincipalDep,
):
    """版本回滚: 切换 is_current_version 标志, 原子操作

    流程:
    1. 校验当前 doc 与 target_doc 同属一个 doc_group
    2. 校验 target_doc.approval_status == PUBLISHED (不能回滚到未发布版本)
    3. 事务内: 当前 version is_current_version=False; target is_current_version=True
    4. 写 KbDocumentApproval (action=ROLLBACK, from/to, 必填 comment)
    5. AuditService 记录 "document.rollback" 事件
    """
    try:
        current_uid = uuid_utils.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 doc_id")
    try:
        target_uid = uuid_utils.UUID(payload.target_doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 target_doc_id")

    current_doc = await db.get(KbDocument, current_uid)
    if current_doc is None:
        raise HTTPException(status_code=404, detail="当前文档不存在")
    if current_doc.is_deleted:
        raise HTTPException(status_code=410, detail="当前文档已删除")

    target_doc = await db.get(KbDocument, target_uid)
    if target_doc is None:
        raise HTTPException(status_code=404, detail="目标版本文档不存在")
    if target_doc.is_deleted:
        raise HTTPException(status_code=410, detail="目标版本已删除")

    # 同 doc_group 校验
    current_group = current_doc.doc_group or str(current_doc.id)
    target_group = target_doc.doc_group or str(target_doc.id)
    if current_group != target_group:
        raise HTTPException(
            status_code=422,
            detail=f"目标版本与当前不属于同一文档组 ({current_group} vs {target_group})",
        )

    # 目标必须是已发布版本
    target_status = target_doc.approval_status
    if hasattr(target_status, "value"):
        target_status_str = target_status.value
    else:
        target_status_str = str(target_status)
    if target_status_str != KbApprovalStatus.PUBLISHED.value:
        raise HTTPException(
            status_code=422,
            detail=f"目标版本必须已发布 (实际 {target_status_str})",
        )

    # 当前已经是 current 时直接返回
    if current_doc.is_current_version and str(current_doc.id) == str(target_doc.id):
        raise HTTPException(status_code=422, detail="该版本已经是当前版本, 无需回滚")

    # 权限: rollback 是高风险操作, 仅 admin 可执行 (P0-2.4: 移除 service 豁免)
    if "admin" not in principal.roles and principal.actor_role != "admin":
        raise HTTPException(status_code=403, detail="ROLLBACK 仅 admin 可执行")

    # P0-2.2: 状态机校验 — 强制 target PUBLISHED → SUPERSEDED
    from kb.security.approval_recorder import get_last_actor, record_approval, validate_or_raise

    # P0-3.A + C: 提取 IP/UA/request_id + operation_id
    ip, ua, rid = extract_request_meta(request)
    operation_id = str(uuid.uuid4())

    # P0-3.B: last_actor 取 target_doc 的最近一次审批人, 兜底 created_by
    last_actor = await get_last_actor(db, target_doc.id) or target_doc.created_by

    # 兼容: 旧 mock 测试里 target_status 是 MagicMock(value=...), 真代码是 enum
    target_status_value = (
        target_status.value if hasattr(target_status, "value") else str(target_status)
    )
    new_status = validate_or_raise(
        current_status=target_status_value,
        action=KbApprovalAction.SUPERSEDE,
        actor_id=principal.actor_id,
        actor_role=principal.actor_role,
        comment=payload.comment,  # SUPERSEDE 强制 comment 校验
        last_actor=last_actor,
    )
    now = datetime.now(timezone.utc)

    # 原子切换 is_current_version
    current_doc.is_current_version = False
    current_doc.updated_at = now
    current_doc.updated_by = principal.actor_id

    target_doc.is_current_version = True
    target_doc.updated_at = now
    target_doc.updated_by = principal.actor_id

    # 写审批记录 (action=SUPERSEDE, high risk) — 复用 approval_recorder
    record = record_approval(
        db,
        doc=target_doc,
        action=KbApprovalAction.SUPERSEDE,
        from_status=target_status,
        to_status=new_status,
        actor_id=principal.actor_id,
        actor_role=principal.actor_role,
        comment=f"[ROLLBACK] {payload.comment} (从 {doc_id} 切到 {payload.target_doc_id})",
        ip=ip,  # P0-3.A
        ua=ua,
        request_id=rid,
        operation_id=operation_id,  # P0-3.C
    )

    # 业务审计
    try:
        audit = AuditService(db)
        await audit.log(
            event_type="document.rollback",
            principal=principal,
            resource=str(target_doc.id),
            action="rollback",
            result="success",
            detail={
                "from_doc_id": str(current_doc.id),
                "to_doc_id": str(target_doc.id),
                "from_version": current_doc.version,
                "to_version": target_doc.version,
                "comment": payload.comment,
            },
            request_id=rid,  # P0-3.A
            ip=ip,
            ua=ua,
            operation_id=operation_id,  # P0-3.C
        )
    except Exception:
        logger.exception("rollback 审计事件记录失败", doc_id=str(target_doc.id))

    await db.commit()

    logger.info(
        "版本回滚完成",
        from_doc_id=str(current_doc.id),
        to_doc_id=str(target_doc.id),
        from_version=current_doc.version,
        to_version=target_doc.version,
        actor_id=principal.actor_id,
        operation_id=operation_id,
    )

    return RollbackResponse(
        from_doc_id=str(current_doc.id),
        to_doc_id=str(target_doc.id),
        from_version=current_doc.version,
        to_version=target_doc.version,
        approval_id=str(record.id),
        actor_id=principal.actor_id,
        created_at=record.created_at.isoformat() if record.created_at else now.isoformat(),
    )


@router.get("/{doc_id}/diff", response_model=DiffResponse)
async def diff_versions(
    doc_id: str,
    db: DbSession,
    principal: PrincipalDep,
    from_doc_id: str = Query(..., alias="from"),
    to_doc_id: str = Query(..., alias="to"),
):
    """两版本 diff — 字段级对比

    对比: version / approval_status / content_hash / llm_summary / llm_keywords / llm_entities
    """
    try:
        from_uid = uuid_utils.UUID(from_doc_id)
        to_uid = uuid_utils.UUID(to_doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 doc_id")

    from_doc = await db.get(KbDocument, from_uid)
    to_doc = await db.get(KbDocument, to_uid)
    if from_doc is None or to_doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 同 doc_group 校验 (跨组 diff 没有意义)
    fg = from_doc.doc_group or str(from_doc.id)
    tg = to_doc.doc_group or str(to_doc.id)
    if fg != tg:
        raise HTTPException(
            status_code=422,
            detail=f"两版本不属于同一文档组 ({fg} vs {tg}), 无法 diff",
        )

    def _str_or_none(v: Any) -> str | None:
        return str(v) if v is not None else None

    fields: list[DiffField] = []
    compare_specs = [
        ("version", from_doc.version, to_doc.version),
        ("approval_status", from_doc.approval_status.value, to_doc.approval_status.value),
        ("content_hash", from_doc.content_hash, to_doc.content_hash),
        ("llm_summary", from_doc.llm_summary, to_doc.llm_summary),
        ("effective_date", _str_or_none(from_doc.effective_date), _str_or_none(to_doc.effective_date)),
        ("expiry_date", _str_or_none(from_doc.expiry_date), _str_or_none(to_doc.expiry_date)),
    ]
    for fname, fv, tv in compare_specs:
        fields.append(
            DiffField(
                field=fname,
                from_value=fv,
                to_value=tv,
                changed=(fv != tv),
            )
        )

    # LLM 字段 (list) — 序列化为 JSON 再 diff
    for fname, fv, tv in [
        ("llm_keywords", from_doc.llm_keywords or [], to_doc.llm_keywords or []),
        ("llm_entities", from_doc.llm_entities or [], to_doc.llm_entities or []),
    ]:
        fs = ",".join(sorted(str(x) for x in fv))
        ts = ",".join(sorted(str(x) for x in tv))
        fields.append(
            DiffField(
                field=fname,
                from_value=fs or None,
                to_value=ts or None,
                changed=(fs != ts),
            )
        )

    # 内容差异占位 (基于 content_hash, 详细 diff 走 chunk 级, I2-C3 不展开)
    content_diff: str | None = None
    if from_doc.content_hash and to_doc.content_hash and from_doc.content_hash != to_doc.content_hash:
        content_diff = f"--- {from_doc_id} (hash={from_doc.content_hash[:8]})\n+++ {to_doc_id} (hash={to_doc.content_hash[:8]})\n@@ 内容已变更 (chunk 级 diff 走 /chunks diff 端点) @@"

    return DiffResponse(
        from_doc_id=from_doc_id,
        to_doc_id=to_doc_id,
        fields=fields,
        content_unified_diff=content_diff,
    )


@router.post("/{doc_id}/takedown", response_model=TakedownResponse)
async def emergency_takedown(
    doc_id: str,
    payload: TakedownRequest,
    request: Request,  # P0-3.A: 透传 IP/UA/request_id
    db: DbSession,
    principal: PrincipalDep,
):
    """紧急下架 — 高风险操作, 仅 admin, 强留痕

    P0-2.2: 走状态机校验, 强制 PUBLISHED/SUPERSEDED → ARCHIVED
    (旧代码允许 DRAFT 直接 takedown, 是漏洞)

    与 /archive 区别:
    - takedown 强制 reason 分类 (regulatory/security/quality/other)
    - takedown 强制 comment 至少 5 字符
    - takedown 额外翻 is_current_version = False (紧急下架往往要立即停用当前版本)
    - retention 5y (跟 archive 统一, 旧代码 10y 过严, P0-2.2 改齐)
    """
    try:
        uid = uuid_utils.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 doc_id")

    # 权限: takedown 是高风险操作, 仅 admin 可执行
    if "admin" not in principal.roles and principal.actor_role != "admin":
        raise HTTPException(status_code=403, detail="TAKEDOWN 仅 admin 可执行")

    doc = await db.get(KbDocument, uid)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.is_deleted:
        raise HTTPException(status_code=410, detail="文档已删除")

    # reason 合法性
    if payload.reason not in {"regulatory", "security", "quality", "other"}:
        raise HTTPException(status_code=422, detail="reason 必须是 regulatory/security/quality/other 之一")

    # P0-2.2: 状态机校验 — 强制 PUBLISHED/SUPERSEDED → ARCHIVED
    from kb.security.approval_recorder import get_last_actor, record_approval, validate_or_raise

    # P0-3.A + C: 提取 IP/UA/request_id + operation_id
    ip, ua, rid = extract_request_meta(request)
    operation_id = str(uuid.uuid4())

    # P0-3.B: last_actor 取 doc 的最近一次审批人, 兜底 created_by
    last_actor = await get_last_actor(db, doc.id) or doc.created_by

    from_status = doc.approval_status
    from_status_value = (
        from_status.value if hasattr(from_status, "value") else str(from_status)
    )
    new_status = validate_or_raise(
        current_status=from_status_value,
        action=KbApprovalAction.ARCHIVE,
        actor_id=principal.actor_id,
        actor_role=principal.actor_role,
        comment=payload.comment,  # ARCHIVE 强制 comment 校验
        last_actor=last_actor,
    )

    # 写审批记录 (action=ARCHIVE, high risk) — 复用 approval_recorder
    record = record_approval(
        db,
        doc=doc,
        action=KbApprovalAction.ARCHIVE,
        from_status=from_status,
        to_status=new_status,
        actor_id=principal.actor_id,
        actor_role=principal.actor_role,
        comment=f"[TAKEDOWN/{payload.reason}] {payload.comment}",
        ip=ip,  # P0-3.A
        ua=ua,
        request_id=rid,
        operation_id=operation_id,  # P0-3.C
    )

    # takedown 额外副作用: 立即停用当前版本 (P0-2.2 与旧行为一致)
    doc.is_current_version = False

    # 业务审计
    try:
        audit = AuditService(db)
        await audit.log(
            event_type="document.takedown",
            principal=principal,
            resource=doc_id,
            action="takedown",
            result="success",
            detail={
                "from_status": from_status.value,
                "to_status": new_status.value,
                "reason": payload.reason,
                "comment": payload.comment,
            },
            request_id=rid,  # P0-3.A
            ip=ip,
            ua=ua,
            operation_id=operation_id,  # P0-3.C
        )
    except Exception:
        logger.exception("takedown 审计事件记录失败", doc_id=doc_id)

    await db.commit()

    logger.warning(
        "紧急下架执行",
        doc_id=doc_id,
        reason=payload.reason,
        actor_id=principal.actor_id,
        comment=payload.comment,
        operation_id=operation_id,
    )

    return TakedownResponse(
        doc_id=doc_id,
        approval_id=str(record.id),
        actor_id=principal.actor_id,
        comment=payload.comment,
        reason=payload.reason,
        created_at=record.created_at.isoformat() if record.created_at else "",
    )
