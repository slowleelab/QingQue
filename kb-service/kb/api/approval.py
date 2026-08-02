"""审批工作流 API (I1-C3)

5 端点:
- POST /api/v1/documents/{id}/submit   DRAFT/REJECTED → IN_REVIEW
- POST /api/v1/documents/{id}/approve  IN_REVIEW → APPROVED (双签)
- POST /api/v1/documents/{id}/reject   IN_REVIEW/APPROVED → REJECTED (需 comment)
- POST /api/v1/documents/{id}/publish  APPROVED → PUBLISHED
- POST /api/v1/documents/{id}/archive  PUBLISHED/SUPERSEDED → ARCHIVED (需 comment, 高风险)
- GET  /api/v1/documents/{id}/approvals  审批历史 (含 actor/role/comment/IP/UA)

所有端点:
- 走 PrincipalDep 鉴权 (JWT/API Key)
- 落 KbDocumentApproval 表 (含 tenant_id/ip/ua/request_id/operation_result/risk_level/retention_until)
- 高风险操作 (archive) 强制 risk_level='high'
- 状态机非法转移 → 422 (WorkflowError)
- 终态文档 (ARCHIVED) → 拒绝所有操作
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import uuid_utils
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from kb.api.deps import DbSession, ESClient, PrincipalDep
from kb.logging import get_logger
from kb.orm.kb import (
    KbApprovalAction,
    KbApprovalStatus,
    KbDocument,
    KbDocumentApproval,
)
from kb.security.approval_recorder import record_approval as _record_approval  # P0-2.2: 单点真相
from kb.security.audit_service import AuditService, extract_request_meta
from kb.security.workflow import (
    WorkflowError,
    get_allowed_actions,
    is_terminal,
    validate_transition,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["approval"])

# 高风险动作 (强制 risk_level='high')
_HIGH_RISK_ACTIONS = {KbApprovalAction.ARCHIVE, KbApprovalAction.SUPERSEDE}

# 业务审计默认留存: 5 年 (GB/T 22239 三级等保)
_AUDIT_RETENTION_YEARS = 5


# ── 请求/响应模型 ──


class WorkflowActionRequest(BaseModel):
    """统一审批动作请求体"""

    comment: str | None = Field(default=None, max_length=2000, description="审批备注 (REJECT/ARCHIVE/SUPERSEDE 必填)")


class WorkflowActionResponse(BaseModel):
    """统一审批动作响应"""

    doc_id: str
    from_status: str
    to_status: str
    action: str
    actor_id: str
    approval_id: str
    created_at: str


class ApprovalRecord(BaseModel):
    """审批历史记录"""

    approval_id: str
    action: str
    from_status: str | None
    to_status: str
    actor_id: str
    actor_role: str
    comment: str | None
    ip: str | None
    ua: str | None
    request_id: str | None
    operation_result: str
    risk_level: str
    created_at: str


class ApprovalListResponse(BaseModel):
    doc_id: str
    current_status: str
    allowed_actions: list[str]
    records: list[ApprovalRecord]


# ── 内部辅助 ──


def _extract_request_meta(request: Request) -> tuple[str | None, str | None, str | None]:
    """P0-3: 薄包装, 委托给 audit_service.extract_request_meta (跨模块复用)"""
    return extract_request_meta(request)


# ── P0-2.3 publish ES 同步 ──


async def _ensure_es_in_sync(
    doc_id: str,
    db: Any,
    es: Any,
    *,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """publish 收尾: 比对 ES doc_count vs PG chunk_count, 不一致就 reindex

    不一致场景:
      - 上传时 ETL 成功但 ES 写失败 (write_chunks_to_es 异常被吞)
      - 上传时 ES 还没起来 (KAFKA_PUBLISH 后 Worker 才跑)
      - 手动 ETL 跳 ES (开发期)
      - 增量索引失败但 KbChunk.es_indexed 仍为 True (边界)

    Returns:
      {"es_count": int, "pg_count": int, "reindexed": bool, "added": int, "skipped": str|None}
      skipped 取值: "es_unavailable" / "no_chunks" / "doc_missing"
    """
    from sqlalchemy import func, select
    from elasticsearch import AsyncElasticsearch

    from kb.api.documents import _build_reindex_metadata
    from kb.config import get_settings
    from kb.orm.kb import KbChunk
    from kb.pipeline.writer import (
        delete_chunks_from_es,
        mark_es_indexed,
        write_chunks_to_es,
    )

    if es is None:
        return {"es_count": 0, "pg_count": 0, "reindexed": False, "added": 0, "skipped": "es_unavailable"}

    try:
        uid = uuid_utils.UUID(doc_id)
    except ValueError:
        return {"es_count": 0, "pg_count": 0, "reindexed": False, "added": 0, "skipped": "doc_missing"}

    doc = await db.get(KbDocument, uid)
    if doc is None:
        return {"es_count": 0, "pg_count": 0, "reindexed": False, "added": 0, "skipped": "doc_missing"}

    # PG 真相
    pg_count = (
        await db.execute(
            select(func.count()).select_from(KbChunk).where(KbChunk.document_id == uid)
        )
    ).scalar() or 0

    if pg_count == 0:
        return {"es_count": 0, "pg_count": 0, "reindexed": False, "added": 0, "skipped": "no_chunks"}

    # ES 现状
    settings = get_settings()
    try:
        if not isinstance(es, AsyncElasticsearch):
            # 不是真 ES 客户端 (测试 mock), 跳过比对
            return {"es_count": 0, "pg_count": pg_count, "reindexed": False, "added": 0, "skipped": "es_unavailable"}
        es_resp = await es.count(
            index=settings.elasticsearch.chunks_index,
            body={"query": {"term": {"doc_id": doc_id}}},
        )
        es_count = es_resp.get("count", 0)
    except Exception:
        logger.exception("publish ES count 查询失败: doc_id=%s", doc_id)
        es_count = -1  # 查询失败视为不一致

    if es_count == pg_count:
        return {"es_count": es_count, "pg_count": pg_count, "reindexed": False, "added": 0, "skipped": None}

    # P0-3.D: ES count 查询异常 (-1) 也算需要重建, 落 partial 审计 (视为失败)
    es_count_error = es_count == -1

    # 重建
    await delete_chunks_from_es(es, doc_id)
    chunks_q = (
        await db.execute(
            select(KbChunk).where(KbChunk.document_id == uid).order_by(KbChunk.chunk_index)
        )
    ).scalars().all()

    from kb.pipeline.writer import deserialize_embedding

    chunk_ids = [str(c.id) for c in chunks_q]
    chunks_data = [
        {
            "content": c.content,
            "chunk_type": c.chunk_type,
            "heading_path": c.heading_path.split(" > ") if c.heading_path else [],
        }
        for c in chunks_q
    ]
    embeddings = [deserialize_embedding(c.embedding) if c.embedding else [] for c in chunks_q]
    metadata = _build_reindex_metadata(doc, doc_id_fallback=doc_id)
    success = await write_chunks_to_es(
        es, doc_id, chunk_ids, chunks_data, embeddings, metadata, chunks_q[0].model_version or "unknown",
    )
    await mark_es_indexed(db, chunk_ids)

    # P0-3.D: 重建部分失败 / ES count 查询异常 → 落 high risk partial 审计
    if success != pg_count or es_count_error:
        from kb.security.approval_recorder import record_approval_partial

        try:
            reason = (
                f"es_count_error" if es_count_error
                else f"es_sync partial: success={success}/{pg_count}"
            )
            record_approval_partial(
                db,
                doc=doc,
                action=KbApprovalAction.PUBLISH,  # 复用动作 (7 状态不变)
                from_status=doc.approval_status,
                to_status=doc.approval_status,  # 状态未变
                actor_id="system",
                actor_role="service",
                comment=(
                    f"{reason}, es_count_before={es_count}, pg_count={pg_count}, "
                    f"op_id={operation_id}"
                ),
                operation_id=operation_id,
                risk_level="high",
            )
            logger.warning(
                "publish es sync 异常, 落 partial 审计",
                doc_id=doc_id,
                success=success,
                pg_count=pg_count,
                es_count_error=es_count_error,
                operation_id=operation_id,
            )
        except Exception:
            logger.exception("es_sync partial 审计落库失败", doc_id=doc_id)

    await db.commit()

    logger.info(
        "publish es sync 重建完成",
        doc_id=doc_id,
        es_count_before=es_count,
        pg_count=pg_count,
        added=success,
        operation_id=operation_id,
    )
    return {"es_count": es_count, "pg_count": pg_count, "reindexed": True, "added": success, "skipped": None}


async def _execute_action(
    doc_id: str,
    action: KbApprovalAction,
    payload: WorkflowActionRequest,
    db: Any,
    principal: Any,
    request: Request,
) -> WorkflowActionResponse:
    """统一动作执行流程: 查文档 → 校验终态 → 状态机校验 → 落审计 → 提交"""
    try:
        uid = uuid_utils.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 doc_id")

    doc = await db.get(KbDocument, uid)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.is_deleted:
        raise HTTPException(status_code=410, detail="文档已删除")

    # 终态保护
    if is_terminal(doc.approval_status):
        raise HTTPException(
            status_code=422,
            detail=f"文档处于终态 {doc.approval_status.value}, 不能执行任何审批动作",
        )

    # 状态机校验 (P0-3.B: last_actor 查最近一次 KbDocumentApproval, 兜底 created_by)
    from kb.security.approval_recorder import get_last_actor

    last_actor = await get_last_actor(db, doc.id) or doc.created_by
    try:
        new_status = validate_transition(
            current_status=doc.approval_status,
            action=action,
            actor_id=principal.actor_id,
            actor_role=principal.actor_role,
            comment=payload.comment,
            last_actor=last_actor,
        )
    except WorkflowError as e:
        # 双签违规是 403, 其余 422
        if e.code == "dual_sign_required":
            raise HTTPException(status_code=403, detail=e.message) from e
        raise HTTPException(status_code=e.http_status, detail=e.message) from e

    # 落审计 + 同步状态 (P0-3.C: operation_id 串联多步)
    ip, ua, rid = _extract_request_meta(request)
    operation_id = str(_uuid.uuid4())
    record = _record_approval(
        db,
        doc=doc,
        action=action,
        from_status=doc.approval_status,
        to_status=new_status,
        actor_id=principal.actor_id,
        actor_role=principal.actor_role,
        comment=payload.comment,
        ip=ip,
        ua=ua,
        request_id=rid,
        operation_id=operation_id,
    )

    # I1-C4: 业务审计事件 (structlog) — operation_id 串联
    try:
        audit = AuditService(db)
        await audit.log(
            event_type=f"document.{action.value.lower()}",
            principal=principal,
            resource=doc_id,
            action=action.value,
            result="success",
            detail={
                "from_status": doc.approval_status.value,
                "to_status": new_status.value,
                "comment": payload.comment,
                "risk_level": record.risk_level,
            },
            request_id=rid,
            ip=ip,
            ua=ua,
            operation_id=operation_id,
        )
    except Exception:
        # 审计日志失败不阻塞主流程
        logger.exception("审批审计事件记录失败", doc_id=doc_id)

    await db.commit()

    logger.info(
        "审批动作完成",
        doc_id=doc_id,
        action=action.value,
        from_status=doc.approval_status.value,
        to_status=new_status.value,
        actor_id=principal.actor_id,
        risk_level=record.risk_level,
    )

    return WorkflowActionResponse(
        doc_id=doc_id,
        from_status=record.from_status or "",
        to_status=new_status.value,
        action=action.value,
        actor_id=principal.actor_id,
        approval_id=str(record.id),
        created_at=record.created_at.isoformat() if record.created_at else "",
    )


# ── 端点 ──


@router.post("/{doc_id}/submit", response_model=WorkflowActionResponse)
async def submit_document(
    doc_id: str,
    payload: WorkflowActionRequest,
    db: DbSession,
    principal: PrincipalDep,
    request: Request,
):
    """DRAFT/REJECTED → IN_REVIEW (编辑提交审核)"""
    return await _execute_action(doc_id, KbApprovalAction.SUBMIT, payload, db, principal, request)


@router.post("/{doc_id}/approve", response_model=WorkflowActionResponse)
async def approve_document(
    doc_id: str,
    payload: WorkflowActionRequest,
    db: DbSession,
    principal: PrincipalDep,
    request: Request,
):
    """IN_REVIEW → APPROVED (审核员, 4-eyes 双签)"""
    return await _execute_action(doc_id, KbApprovalAction.APPROVE, payload, db, principal, request)


@router.post("/{doc_id}/reject", response_model=WorkflowActionResponse)
async def reject_document(
    doc_id: str,
    payload: WorkflowActionRequest,
    db: DbSession,
    principal: PrincipalDep,
    request: Request,
):
    """IN_REVIEW/APPROVED → REJECTED (需 comment)"""
    return await _execute_action(doc_id, KbApprovalAction.REJECT, payload, db, principal, request)


@router.post("/{doc_id}/publish", response_model=WorkflowActionResponse)
async def publish_document(
    doc_id: str,
    payload: WorkflowActionRequest,
    db: DbSession,
    es: ESClient,
    principal: PrincipalDep,
    request: Request,
):
    """APPROVED → PUBLISHED (复核发布) + P0-2.3 同步 ES 校验重建

    状态机: APPROVED → PUBLISHED (合规留痕)
    ES 同步: publish 完成后比对 ES doc_count vs PG chunk_count,
             不一致就调 reindex 重建 (PG 真理 + 已有 embedding, 不重跑 LLM)
    """
    resp = await _execute_action(doc_id, KbApprovalAction.PUBLISH, payload, db, principal, request)
    # P0-3.C: 从响应里拿 approval_id 派生 operation_id, 串联 _ensure_es_in_sync 的审计
    op_id = f"publish:{resp.approval_id}"
    sync = await _ensure_es_in_sync(doc_id, db, es, operation_id=op_id)
    if sync.get("reindexed"):
        logger.info("publish es sync 重建", doc_id=doc_id, added=sync["added"], operation_id=op_id)
    elif sync.get("skipped"):
        logger.info("publish es sync 跳过", doc_id=doc_id, reason=sync["skipped"], operation_id=op_id)
    # 响应保持 WorkflowActionResponse 统一, 同步结果走 logger + 审计事件
    return resp


@router.post("/{doc_id}/archive", response_model=WorkflowActionResponse)
async def archive_document(
    doc_id: str,
    payload: WorkflowActionRequest,
    db: DbSession,
    principal: PrincipalDep,
    request: Request,
):
    """PUBLISHED/SUPERSEDED → ARCHIVED (紧急下架, admin only, 需 comment)"""
    # ARCHIVE 是高风险动作, 仅 admin 可操作
    if "admin" not in principal.roles and principal.actor_role != "admin":
        raise HTTPException(status_code=403, detail="ARCHIVE 仅 admin 可执行")
    return await _execute_action(doc_id, KbApprovalAction.ARCHIVE, payload, db, principal, request)


@router.get("/{doc_id}/approvals", response_model=ApprovalListResponse)
async def list_approvals(
    doc_id: str,
    db: DbSession,
    principal: PrincipalDep,
    limit: int = 50,
    offset: int = 0,
):
    """查询审批历史 (含完整审计三元组)"""
    try:
        uid = uuid_utils.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 doc_id")

    doc = await db.get(KbDocument, uid)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    limit = min(limit, 200)
    offset = max(offset, 0)

    query = (
        select(KbDocumentApproval)
        .where(KbDocumentApproval.document_id == uid)
        .order_by(KbDocumentApproval.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    records = result.scalars().all()

    return ApprovalListResponse(
        doc_id=doc_id,
        current_status=doc.approval_status.value,
        allowed_actions=[a.value for a in get_allowed_actions(doc.approval_status)],
        records=[
            ApprovalRecord(
                approval_id=str(r.id),
                action=r.action.value,
                from_status=r.from_status,
                to_status=r.to_status,
                actor_id=r.actor_id,
                actor_role=r.actor_role,
                comment=r.comment,
                ip=r.ip,
                ua=r.ua,
                request_id=r.request_id,
                operation_result=r.operation_result,
                risk_level=r.risk_level,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in records
        ],
    )


# ── 待审批队列 (P0-2.1) ──


class PendingApprovalItem(BaseModel):
    """待审批文档摘要"""

    doc_id: str
    title: str
    category: str | None
    approval_status: str
    updated_at: str | None
    current_version: str
    last_actor_id: str | None
    last_action_at: str | None


class PendingApprovalListResponse(BaseModel):
    items: list[PendingApprovalItem]
    total: int
    limit: int
    offset: int


@router.get("/approvals/pending", response_model=PendingApprovalListResponse)
async def list_pending_approvals(
    db: DbSession,
    principal: PrincipalDep,
    status: str = Query("IN_REVIEW", description="过滤的审批状态: DRAFT/IN_REVIEW/APPROVED/REJECTED/PUBLISHED"),
    limit: int = 50,
    offset: int = 0,
):
    """待审批队列 — 审核员工作台

    默认 status=IN_REVIEW (待审核); 同一接口可查 DRAFT (草稿) / APPROVED (待发布) /
    REJECTED (已驳回) / PUBLISHED (已发布). 终态 ARCHIVED 不在本接口暴露
    (用 /documents?status=ARCHIVED 查文档列表).

    租户隔离: 强制 principal.tenant_id, 不接受 query 覆盖 (P0-1 一致).
    """
    try:
        target = KbApprovalStatus(status)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"未知 status: {status}, 必须是 DRAFT/IN_REVIEW/APPROVED/REJECTED/PUBLISHED",
        )

    if target == KbApprovalStatus.ARCHIVED:
        # 终态不走队列, 避免误把已下架文档推送给审核员
        raise HTTPException(status_code=422, detail="ARCHIVED 是终态, 不进入待审批队列")

    limit = min(limit, 200)
    offset = max(offset, 0)

    base_query = (
        select(KbDocument)
        .where(KbDocument.is_deleted.is_(False))
        .where(KbDocument.tenant_id == principal.tenant_id)
        .where(KbDocument.approval_status == target)
    )

    # total: 不受 limit/offset 影响
    total = (
        await db.execute(select(func.count()).select_from(base_query.subquery()))
    ).scalar_one()

    docs = (
        await db.execute(
            base_query.order_by(KbDocument.updated_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()

    # 副表: 每个 doc 的最近一次审批 (单次 IN 查询, 避免 N+1)
    last_by_doc: dict[Any, tuple[str | None, Any]] = {}
    if docs:
        doc_ids = [d.id for d in docs]
        latest_q = (
            select(
                KbDocumentApproval.document_id,
                KbDocumentApproval.actor_id,
                KbDocumentApproval.created_at,
            )
            .where(KbDocumentApproval.document_id.in_(doc_ids))
            .order_by(KbDocumentApproval.created_at.desc())
        )
        rows = (await db.execute(latest_q)).all()
        for doc_id, actor_id, created_at in rows:
            if doc_id not in last_by_doc:
                last_by_doc[doc_id] = (actor_id, created_at)

    return PendingApprovalListResponse(
        items=[
            PendingApprovalItem(
                doc_id=str(d.id),
                title=d.title or "",
                category=d.category,
                approval_status=d.approval_status.value,
                updated_at=d.updated_at.isoformat() if d.updated_at else None,
                current_version=d.version or "1.0",
                last_actor_id=last_by_doc.get(d.id, (None, None))[0],
                last_action_at=(
                    last_by_doc[d.id][1].isoformat() if last_by_doc.get(d.id, (None, None))[1] else None
                ),
            )
            for d in docs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
