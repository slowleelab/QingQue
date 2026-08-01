"""管理 / 运维端点

- GET  /api/v1/diagnostics      — 阶段耗时统计 + 服务健康
- POST /api/v1/admin/reindex-all — 重建全部已发布文档的 ES 索引
- POST /api/v1/admin/clear-cache — 清空 Redis 检索缓存

所有端点要 API Key 认证 (复用 ApiKeyDep).
"""

from __future__ import annotations

import logging
import uuid_utils
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from kb.api.deps import ApiKeyDep, DbSession
from kb.orm.kb import (
    KbApprovalStatus,
    KbChunk,
    KbDocument,
    KbDocumentApproval,
    KbIngestionLog,
    KbIngestionStage,
    KbRetrievalAudit,
)
from kb.storage.redis import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


# ── 1. 诊断 / 统计 ──

@router.get("/api/v1/diagnostics")
async def diagnostics(
    db: DbSession,
    _api_key: ApiKeyDep,
    request: Request,
) -> dict[str, Any]:
    """诊断端点 — 给运维 / 监控用

    返回:
    - 各阶段 ETL 耗时统计 (avg/p95/max 毫秒, 基于近 7 天日志)
    - 各阶段失败率
    - 文档分布 (按 status / approval_status)
    - 依赖健康快照
    - 嵌入熔断器状态
    """
    # ── 阶段耗时统计 ──
    stage_stats: dict[str, dict[str, float | int]] = {}
    try:
        # 按阶段聚合 duration_ms (按 created_at 近 7 天过滤, schema 没有 started_at)
        from datetime import datetime, timedelta, timezone
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        query = (
            select(
                KbIngestionLog.stage,
                func.count().label("n"),
                func.avg(KbIngestionLog.duration_ms).label("avg_ms"),
                func.max(KbIngestionLog.duration_ms).label("max_ms"),
                func.sum(
                    func.case((KbIngestionLog.status == "failed", 1), else_=0)
                ).label("failures"),
            )
            .where(KbIngestionLog.created_at >= seven_days_ago)
            .group_by(KbIngestionLog.stage)
        )
        result = await db.execute(query)
        for row in result.all():
            stage = row.stage.value if hasattr(row.stage, "value") else str(row.stage)
            n = int(row.n or 0)
            failures = int(row.failures or 0)
            stage_stats[stage] = {
                "samples": n,
                "avg_ms": round(float(row.avg_ms or 0), 1),
                "max_ms": int(row.max_ms or 0),
                "failure_rate": round(failures / n, 4) if n else 0.0,
            }
    except Exception as e:
        logger.warning("阶段统计查询失败", error=str(e))
        stage_stats = {"_error": {"message": str(e)}}

    # ── 文档分布 ──
    doc_distribution: dict[str, int] = {}
    try:
        result = await db.execute(
            select(KbDocument.status, func.count())
            .where(KbDocument.is_deleted.is_(False))
            .group_by(KbDocument.status)
        )
        for status, count in result.all():
            doc_distribution[status.value if hasattr(status, "value") else str(status)] = int(count)
    except Exception as e:
        logger.warning("文档分布查询失败", error=str(e))

    # ── 依赖健康快照 ──
    health: dict[str, str] = {}

    try:
        from sqlalchemy import text
        from kb.database import get_engine
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        health["postgres"] = "ok"
    except Exception as e:
        health["postgres"] = f"fail: {e.__class__.__name__}"

    es = getattr(request.app.state, "es_client", None)
    if es:
        try:
            health["elasticsearch"] = "ok" if await es.ping() else "fail: ping"
        except Exception as e:
            health["elasticsearch"] = f"fail: {e.__class__.__name__}"
    else:
        health["elasticsearch"] = "uninitialized"

    try:
        redis = get_redis()
        health["redis"] = "ok" if (redis and await redis.ping()) else "fail"
    except Exception as e:
        health["redis"] = f"fail: {e.__class__.__name__}"

    # ── 嵌入熔断器状态 ──
    breaker = getattr(request.app.state, "embedding_breaker", None)
    breaker_status = {
        "available": breaker.is_available if breaker else None,
        "consecutive_failures": breaker._consecutive_failures if breaker else None,  # noqa: SLF001
        "consecutive_successes": breaker._consecutive_successes if breaker else None,  # noqa: SLF001
    }

    return {
        "stage_stats": stage_stats,
        "doc_distribution": doc_distribution,
        "health": health,
        "embedding_breaker": breaker_status,
    }


# ── 2. 批量重建索引 ──

@router.post("/api/v1/admin/reindex-all")
async def reindex_all(
    db: DbSession,
    _api_key: ApiKeyDep,
    es: Any,  # ESClient (注入)
    limit: int = 100,
) -> dict[str, Any]:
    """重建全部 PUBLISHED 文档的 ES 索引

    用途: ES 索引模板升级 / 数据漂移修复 / 切到新嵌入模型后的灰度切换.
    后台异步执行, 端点只返回计划数, 不阻塞 API.

    限制: 一次最多 limit 个 (默认 100), 大批量应分批调用.
    """
    limit = min(limit, 500)  # 上限保护

    # 找出全部已发布文档
    query = (
        select(KbDocument)
        .where(KbDocument.is_deleted.is_(False))
        .where(KbDocument.approval_status == KbApprovalStatus.PUBLISHED)
        .where(KbDocument.chunk_count > 0)
        .order_by(KbDocument.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    docs = result.scalars().all()

    if not docs:
        return {"planned": 0, "message": "无已发布文档"}

    # 投递到 Kafka (复用 ingest 流程, metadata 标记 reindex 来源)
    from kb.storage.kafka import publish_ingest_request

    planned = 0
    for doc in docs:
        try:
            payload = {
                "doc_id": str(doc.id),
                "file_path": doc.file_path,
                "source_type": doc.source_type.value if hasattr(doc.source_type, "value") else str(doc.source_type),
                "metadata": {
                    "title": doc.title,
                    "category": doc.category,
                    "doc_type": doc.doc_type,
                    "card_type": doc.card_type or "",
                    "customer_tier": doc.customer_tier or "",
                    "security_level": doc.security_level,
                    "version": doc.version,
                    "keywords": doc.llm_keywords or [],
                    "approval_status": "PUBLISHED",
                    "is_current_version": doc.is_current_version,
                    "doc_group": doc.doc_group or str(doc.id),
                    "_source": "admin_reindex_all",
                },
            }
            await publish_ingest_request(str(doc.id), payload)
            planned += 1
        except Exception as e:
            logger.warning("reindex-all 投递失败", doc_id=str(doc.id), error=str(e))

    return {
        "planned": planned,
        "total_candidates": len(docs),
        "message": f"已投递 {planned} 个文档到 ETL 队列",
    }


# ── 3. 清空检索缓存 ──

@router.post("/api/v1/admin/clear-cache")
async def clear_cache(_api_key: ApiKeyDep) -> dict[str, Any]:
    """清空 Redis 检索缓存

    用途: 数据更新后强制下一轮 query 重新打 ES (而不是吃旧缓存).
    """
    redis = get_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis 不可用")

    # 知识库缓存统一前缀, 见 retrieval/engine.py: _build_cache_key (kp:rag:cache:*)
    deleted = 0
    async for key in redis.scan_iter(match="kp:rag:cache:*"):
        await redis.delete(key)
        deleted += 1

    return {
        "deleted_keys": deleted,
        "message": f"已清理 {deleted} 条检索缓存",
    }


# ── 4. 业务审计查询 (I1-C5) ──


@router.get("/api/v1/admin/business-audit")
async def business_audit(
    db: DbSession,
    _api_key: ApiKeyDep,
    doc_id: str | None = None,
    actor_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """业务审计查询 — 审批流水 + 检索事件

    用于合规审计 / 内部调查, 返回:
    - approvals: 文档审批记录 (按 doc_id/actor_id 过滤)
    - retrievals: 检索事件 (按 actor_id 过滤, query 仅返回 hash 不返回原文)
    - summary: 计数汇总

    I1-C5: admin 端点 1 处, 不暴露 PII (query_hash 是 md5)
    """
    limit = min(limit, 200)

    # 审批记录
    approval_q = select(KbDocumentApproval).order_by(KbDocumentApproval.created_at.desc()).limit(limit)
    if doc_id:
        try:
            uid = uuid_utils.UUID(doc_id)
            approval_q = approval_q.where(KbDocumentApproval.document_id == uid)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的 doc_id")
    if actor_id:
        approval_q = approval_q.where(KbDocumentApproval.actor_id == actor_id)
    approval_result = await db.execute(approval_q)
    approvals = approval_result.scalars().all()

    # 检索事件
    retrieval_q = select(KbRetrievalAudit).order_by(KbRetrievalAudit.created_at.desc()).limit(limit)
    if actor_id:
        retrieval_q = retrieval_q.where(KbRetrievalAudit.actor_id == actor_id)
    retrieval_result = await db.execute(retrieval_q)
    retrievals = retrieval_result.scalars().all()

    return {
        "summary": {
            "approvals_returned": len(approvals),
            "retrievals_returned": len(retrievals),
        },
        "approvals": [
            {
                "approval_id": str(a.id),
                "document_id": str(a.document_id),
                "action": a.action.value if hasattr(a.action, "value") else str(a.action),
                "from_status": a.from_status,
                "to_status": a.to_status,
                "actor_id": a.actor_id,
                "actor_role": a.actor_role,
                "comment": a.comment,
                "ip": a.ip,
                "request_id": a.request_id,
                "risk_level": a.risk_level,
                "operation_result": a.operation_result,
                "tenant_id": a.tenant_id,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in approvals
        ],
        "retrievals": [
            {
                "audit_id": str(r.id),
                "actor_id": r.actor_id,
                "tenant_id": r.tenant_id,
                "query_hash": r.query_hash,
                "top_k": r.top_k,
                "result_count": r.result_count,
                "latency_ms": r.latency_ms,
                "search_type": r.search_type,
                "degraded": r.degraded,
                "request_id": r.request_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in retrievals
        ],
    }
