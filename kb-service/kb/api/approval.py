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

from datetime import UTC, datetime, timedelta
from typing import Any

import uuid_utils
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from kb.api.deps import DbSession, PrincipalDep
from kb.logging import get_logger
from kb.orm.kb import (
    KbApprovalAction,
    KbApprovalStatus,
    KbDocument,
    KbDocumentApproval,
)
from kb.security.approval_recorder import record_approval as _record_approval  # P0-2.2: 单点真相
from kb.security.audit_service import AuditService
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
    """从 FastAPI Request 提取 IP / UA / request_id (与 AuditMiddleware 字段保持一致)"""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    rid = request.headers.get("x-request-id") or getattr(request.state, "request_id", None)
    return ip, ua, rid


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

    # 状态机校验
    try:
        new_status = validate_transition(
            current_status=doc.approval_status,
            action=action,
            actor_id=principal.actor_id,
            actor_role=principal.actor_role,
            comment=payload.comment,
            last_actor=doc.created_by,
        )
    except WorkflowError as e:
        # 双签违规是 403, 其余 422
        if e.code == "dual_sign_required":
            raise HTTPException(status_code=403, detail=e.message) from e
        raise HTTPException(status_code=e.http_status, detail=e.message) from e

    # 落审计 + 同步状态
    ip, ua, rid = _extract_request_meta(request)
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
    )

    # I1-C4: 业务审计事件 (structlog)
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
    principal: PrincipalDep,
    request: Request,
):
    """APPROVED → PUBLISHED (复核发布)"""
    return await _execute_action(doc_id, KbApprovalAction.PUBLISH, payload, db, principal, request)


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
