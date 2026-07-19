"""FAQ 管理 API 路由

提供 FAQ CRUD、审批工作流、检索、分析端点。
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from smartcs.services.common.faq_service import (
    check_faq_duplicate,
    create_faq,
    delete_faq,
    get_faq,
    list_faqs,
    search_faq,
    transition_faq_approval,
    update_faq,
)
from smartcs.shared.auth import CurrentUser
from smartcs.shared.exceptions import SmartCSError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["faq"])


# ── 请求模型 ──


class FaqCreateRequest(BaseModel):
    question: str = Field(..., max_length=512)
    answer: str
    variant_questions: list[str] = Field(default_factory=list)
    category: str
    card_types: list[str] = Field(default_factory=list)
    customer_tiers: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    sort_order: int = 0
    effective_date: str | None = None
    expiry_date: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    regulatory_tags: list[str] = Field(default_factory=list)


class FaqUpdateRequest(BaseModel):
    question: str | None = None
    answer: str | None = None
    variant_questions: list[str] | None = None
    category: str | None = None
    card_types: list[str] | None = None
    keywords: list[str] | None = None
    sort_order: int | None = None


class FaqApprovalRequest(BaseModel):
    comment: str = ""


class FaqSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    card_type: str | None = None


# ── CRUD ──


@router.post("/kb/faq")
async def create_faq_endpoint(body: FaqCreateRequest, request: Request, user: CurrentUser):
    """创建 FAQ"""
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise SmartCSError(code=5001, message="数据库未就绪")

    # 语义去重检测
    embedding_provider = getattr(request.app.state, "embedding_provider", None)
    embedding_breaker = getattr(request.app.state, "embedding_breaker", None)
    if embedding_breaker and embedding_breaker.is_available:
        embedding_provider = embedding_breaker.provider
    milvus_collection = getattr(request.app.state, "milvus_collection", None)

    duplicates = await check_faq_duplicate(body.question, embedding_provider, milvus_collection)
    if duplicates:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": 3006,
                    "message": "FAQ 与已有内容高度相似，请确认是否重复",
                    "type": "FaqDuplicateWarning",
                },
                "duplicates": duplicates,
            },
        )

    faq = await create_faq(
        session_factory,
        question=body.question,
        answer=body.answer,
        variant_questions=body.variant_questions,
        category=body.category,
        card_types=body.card_types,
        customer_tiers=body.customer_tiers,
        keywords=body.keywords,
        effective_date=date.fromisoformat(body.effective_date) if body.effective_date else None,
        expiry_date=date.fromisoformat(body.expiry_date) if body.expiry_date else None,
        allowed_roles=body.allowed_roles,
        regulatory_tags=body.regulatory_tags,
        created_by=user.user_id,
    )
    return {"faq_id": str(faq.id), "approval_status": faq.approval_status}


@router.get("/kb/faq")
async def list_faqs_endpoint(
    request: Request,
    category: str | None = None,
    approval_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """FAQ 列表"""
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise SmartCSError(code=5001, message="数据库未就绪")

    faqs, total = await list_faqs(
        session_factory,
        category=category,
        approval_status=approval_status,
        limit=limit,
        offset=offset,
    )
    return {"faqs": faqs, "total": total}


@router.get("/kb/faq/{faq_id}")
async def get_faq_endpoint(faq_id: str, request: Request):
    """FAQ 详情"""
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise SmartCSError(code=5001, message="数据库未就绪")

    faq = await get_faq(session_factory, faq_id)
    if not faq:
        raise SmartCSError(code=2001, message=f"FAQ 不存在: {faq_id}")
    return faq


@router.put("/kb/faq/{faq_id}")
async def update_faq_endpoint(faq_id: str, body: FaqUpdateRequest, request: Request, user: CurrentUser):
    """更新 FAQ"""
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise SmartCSError(code=5001, message="数据库未就绪")

    updated = await update_faq(
        session_factory,
        faq_id,
        question=body.question,
        answer=body.answer,
        variant_questions=body.variant_questions,
        category=body.category,
        card_types=body.card_types,
        keywords=body.keywords,
        sort_order=body.sort_order,
        updated_by=user.user_id,
    )
    if not updated:
        raise SmartCSError(code=2001, message=f"FAQ 不存在: {faq_id}")
    return {"status": "ok"}


@router.delete("/kb/faq/{faq_id}")
async def delete_faq_endpoint(faq_id: str, request: Request, user: CurrentUser):
    """删除 FAQ（软删除）"""
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise SmartCSError(code=5001, message="数据库未就绪")

    deleted = await delete_faq(session_factory, faq_id)
    if not deleted:
        raise SmartCSError(code=2001, message=f"FAQ 不存在: {faq_id}")
    return {"status": "ok"}


# ── 审批工作流 ──


@router.post("/kb/faq/{faq_id}/submit")
async def submit_faq(faq_id: str, body: FaqApprovalRequest, request: Request, user: CurrentUser):
    """提交审核 (DRAFT → IN_REVIEW)"""
    return await _do_approval(faq_id, "IN_REVIEW", body.comment, request, user)


@router.post("/kb/faq/{faq_id}/approve")
async def approve_faq(faq_id: str, body: FaqApprovalRequest, request: Request, user: CurrentUser):
    """审核通过 (IN_REVIEW → APPROVED)"""
    return await _do_approval(faq_id, "APPROVED", body.comment, request, user)


@router.post("/kb/faq/{faq_id}/reject")
async def reject_faq(faq_id: str, body: FaqApprovalRequest, request: Request, user: CurrentUser):
    """审核驳回 (IN_REVIEW → REJECTED)"""
    return await _do_approval(faq_id, "REJECTED", body.comment, request, user)


@router.post("/kb/faq/{faq_id}/publish")
async def publish_faq(faq_id: str, body: FaqApprovalRequest, request: Request, user: CurrentUser):
    """发布 (APPROVED → PUBLISHED)"""
    return await _do_approval(faq_id, "PUBLISHED", body.comment, request, user)


@router.post("/kb/faq/{faq_id}/archive")
async def archive_faq(faq_id: str, body: FaqApprovalRequest, request: Request, user: CurrentUser):
    """归档 (→ ARCHIVED)"""
    return await _do_approval(faq_id, "ARCHIVED", body.comment, request, user)


async def _do_approval(faq_id: str, target: str, comment: str, request: Request, user: CurrentUser):
    session_factory = getattr(request.app.state, "db_session_factory", None)
    redis_client = getattr(request.app.state, "redis_client", None)
    if not session_factory:
        raise SmartCSError(code=5001, message="数据库未就绪")

    return await transition_faq_approval(
        session_factory,
        faq_id,
        target,
        actor_id=user.user_id,
        actor_role=user.role,
        comment=comment,
        redis_client=redis_client,
    )


# ── 检索 ──


@router.post("/kb/faq/search")
async def search_faq_endpoint(body: FaqSearchRequest, request: Request, user: CurrentUser):
    """FAQ 检索

    串行短路: 精确匹配 → 语义匹配 → miss
    """
    session_factory = getattr(request.app.state, "db_session_factory", None)
    redis_client = getattr(request.app.state, "redis_client", None)

    embedding_provider = None
    embedding_breaker = getattr(request.app.state, "embedding_breaker", None)
    if embedding_breaker and embedding_breaker.is_available:
        embedding_provider = embedding_breaker.provider
    milvus_collection = getattr(request.app.state, "milvus_collection", None)

    result = await search_faq(
        body.query,
        redis_client,
        embedding_provider,
        milvus_collection,
        user_role=user.role,
        card_type=body.card_type,
        top_k=body.top_k,
        session_factory=session_factory,
    )
    return result


# ── 批量导入 ──


class FaqBatchItem(BaseModel):
    question: str
    answer: str
    variant_questions: list[str] = Field(default_factory=list)
    category: str
    card_types: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class FaqBatchRequest(BaseModel):
    items: list[FaqBatchItem]


@router.post("/kb/faq/batch")
async def batch_import(body: FaqBatchRequest, request: Request, user: CurrentUser):
    """批量导入 FAQ"""
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if not session_factory:
        raise SmartCSError(code=5001, message="数据库未就绪")

    created_ids = []
    failed = []
    for i, item in enumerate(body.items):
        try:
            faq = await create_faq(
                session_factory,
                question=item.question,
                answer=item.answer,
                variant_questions=item.variant_questions,
                category=item.category,
                card_types=item.card_types,
                keywords=item.keywords,
                created_by=user.user_id,
            )
            created_ids.append(str(faq.id))
        except Exception as e:
            failed.append({"index": i, "question": item.question[:50], "error": str(e)})

    return {"created": len(created_ids), "failed": len(failed), "ids": created_ids, "errors": failed}
