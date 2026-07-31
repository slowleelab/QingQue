"""管理 / 运维端点

- GET  /api/v1/diagnostics      — 阶段耗时统计 + 服务健康
- POST /api/v1/admin/reindex-all — 重建全部已发布文档的 ES 索引
- POST /api/v1/admin/clear-cache — 清空 Redis 检索缓存

所有端点要 API Key 认证 (复用 ApiKeyDep).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from kb.api.deps import ApiKeyDep, DbSession
from kb.orm.kb import KbApprovalStatus, KbChunk, KbDocument, KbIngestionLog, KbIngestionStage
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
        # 按阶段聚合 duration_ms
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
            .where(KbIngestionLog.started_at.is_not(None))
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

    # 知识库缓存统一前缀, 见 retrieval/engine.py: _build_cache_key
    deleted = 0
    async for key in redis.scan_iter(match="kb:retrieve:*"):
        await redis.delete(key)
        deleted += 1

    return {
        "deleted_keys": deleted,
        "message": f"已清理 {deleted} 条检索缓存",
    }
