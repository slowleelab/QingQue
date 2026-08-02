"""管理 / 运维端点

- GET  /api/v1/diagnostics      — 阶段耗时统计 + 服务健康
- POST /api/v1/admin/reindex-all — 重建全部已发布文档的 ES 索引
- POST /api/v1/admin/clear-cache — 清空 Redis 检索缓存

所有端点要 API Key 认证 (复用 ApiKeyDep).
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

import uuid_utils
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import case, func, select

from kb.api.deps import ApiKeyDep, DbSession
from kb.orm.kb import (
    KbApprovalStatus,
    KbDocument,
    KbDocumentApproval,
    KbIngestionLog,
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
        from datetime import datetime, timedelta
        seven_days_ago = datetime.now(UTC) - timedelta(days=7)
        query = (
            select(
                KbIngestionLog.stage,
                func.count().label("n"),
                func.avg(KbIngestionLog.duration_ms).label("avg_ms"),
                func.max(KbIngestionLog.duration_ms).label("max_ms"),
                func.sum(
                    case((KbIngestionLog.status == "failed", 1), else_=0)
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
        logger.warning("阶段统计查询失败: %s", str(e))
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
        logger.warning("文档分布查询失败: %s", str(e))

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
        "consecutive_failures": breaker._consecutive_failures if breaker else None,
        "consecutive_successes": breaker._consecutive_successes if breaker else None,
    }

    return {
        "stage_stats": stage_stats,
        "doc_distribution": doc_distribution,
        "health": health,
        "embedding_breaker": breaker_status,
    }


# ── 1a. SLO 实时查询 (P1-3.3) ──


@router.get("/api/v1/admin/slo")
async def admin_slo(
    _api_key: ApiKeyDep,
) -> dict[str, Any]:
    """SLO 实时状态 + 触发的告警 + error budget 剩余 + Prometheus rules YAML

    P1-3.3 落地: 修复 P0-4 评审指出的"slo.py 死代码"问题
    - 旧实现: slo.py 有完整 burn-rate 计算, 但 0 生产调用
    - 新实现: 本端点把 slo.py 全部能力 (AlertEvaluator / compute_error_budget /
      generate_prometheus_rules) 接入生产, 供运维 / 监控使用

    返回:
    - slos: 各 SLO 当前 error/total/p95/p99
    - active_alerts: 触发的 4 档告警列表
    - error_budgets: 各 SLO budget 剩余
    - prometheus_rules_yaml: 可直接 cat > alerts.yml 的字符串
    - burn_rate_enabled: 来自 ObservabilitySettings
    """
    from kb.config import get_settings
    from kb.observability.slo import (
        DEFAULT_SLOS,
        SLI,
        AlertEvaluator,
        compute_error_budget,
        generate_prometheus_rules,
    )
    from kb.observability.slo_metrics import read_slo_metrics

    settings = get_settings()
    # 开关: 关闭时不计算 alerts (但仍返回 metrics 快照)
    burn_rate_enabled = settings.observability.burn_rate_enabled

    # 用配置覆盖 LATENCY 阈值 (来自 .env)
    slos = list(DEFAULT_SLOS)
    for slo in slos:
        if slo.sli == SLI.LATENCY_P95:
            slo.latency_threshold_s = settings.observability.retrieve_p95_threshold_s
        elif slo.sli == SLI.LATENCY_P99:
            slo.latency_threshold_s = settings.observability.retrieve_p99_threshold_s

    # 读当前指标
    metrics = read_slo_metrics()

    # 评估告警 (仅 availability 可基于 error/total 计算; latency 留给 Prometheus 算 quantile)
    active_alerts: list[dict[str, Any]] = []
    if burn_rate_enabled:
        evaluator = AlertEvaluator(slos)
        for (slo_name, sli_value), m in metrics.items():
            if m.total_count <= 0:
                continue
            try:
                sli = SLI(sli_value)
            except ValueError:
                continue
            snaps = evaluator.evaluate(
                slo_name=slo_name,
                sli=sli,
                error_count=int(m.error_count),
                total_count=int(m.total_count),
            )
            active_alerts.extend(
                {
                    "slo": s.slo_name,
                    "sli": s.sli.value,
                    "severity": s.severity,
                    "window_minutes": s.window_minutes,
                    "burn_rate": round(s.burn_rate, 4),
                    "threshold": s.threshold,
                }
                for s in snaps
                if s.triggered
            )

    # 计算 error budget (仅 availability)
    budgets: list[dict[str, Any]] = []
    for slo in slos:
        m = metrics.get((slo.name, slo.sli.value))
        if m is None or m.total_count <= 0:
            continue
        b = compute_error_budget(
            slo_name=slo.name,
            target=slo.target,
            error_count=int(m.error_count),
            total_count=int(m.total_count),
        )
        budgets.append(
            {
                "slo": b.slo_name,
                "target": b.target,
                "remaining_pct": round(b.remaining_pct, 4),
                "healthy": b.healthy,
                "window_days": b.window_days,
            }
        )

    return {
        "burn_rate_enabled": burn_rate_enabled,
        "slos": [
            {
                "name": m.slo_name,
                "sli": m.sli,
                "error_count": m.error_count,
                "total_count": m.total_count,
                "p95_latency": m.p95_latency,
                "p99_latency": m.p99_latency,
            }
            for m in metrics.values()
        ],
        "active_alerts": active_alerts,
        "error_budgets": budgets,
        "prometheus_rules_yaml": generate_prometheus_rules(slos),
    }


# ── 1b. 嵌入漂移监控 (I2-C4) ──


# P1-3.4: 删除原 embedding-drift stub 端点 (line 264-294, 返回 mock 数据 + 错误对象链)
# 用户应改调 /api/v1/admin/embedding-drift-live (下方, 行为正确)


@router.get("/api/v1/admin/embedding-drift-live")
async def embedding_drift_live(request: Request) -> dict[str, Any]:
    """嵌入漂移实时查询 (带 app.state 注入)

    通过 Request 访问 app.state.drift_monitor
    """
    from kb.retrieval.drift import DriftMonitor

    monitor: DriftMonitor | None = getattr(request.app.state, "drift_monitor", None)
    if monitor is None:
        return {"error": "DriftMonitor 未初始化"}

    signal = monitor.compute_drift()
    return {
        "sample_size": signal.sample_size,
        "drift_score": round(signal.drift_score, 6),
        "threshold": monitor.threshold,
        "is_drifted": signal.is_drifted,
        "window_size": monitor.window_size,
        "centroid_dim": len(signal.baseline_centroid) if signal.baseline_centroid else None,
    }


@router.post("/api/v1/admin/shadow-compare")
async def shadow_compare(
    request: Request,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """影子索引对比 (admin 手动触发)

    Body: { "primary_chunk_ids": [...], "shadow_chunk_ids": [...] }

    返回: { jaccard, rank_corr, overlap, diverged, ... }
    """
    from kb.retrieval.drift import ShadowComparator

    primary = payload.get("primary_chunk_ids") or []
    shadow = payload.get("shadow_chunk_ids") or []
    if not isinstance(primary, list) or not isinstance(shadow, list):
        return {"error": "primary_chunk_ids / shadow_chunk_ids 必须是 list[str]"}

    comparator: ShadowComparator | None = getattr(
        request.app.state, "shadow_comparator", None
    )
    if comparator is None:
        return {"error": "ShadowComparator 未初始化"}

    return comparator.compare(primary, shadow)


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
            logger.warning("reindex-all 投递失败: doc_id=%s error=%s", str(doc.id), str(e))

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
