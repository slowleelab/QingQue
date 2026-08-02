"""检索 API

POST /retrieve: 混合检索 (ES 原生 RRF + Reranker)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from kb.api.deps import (
    DbSession,
    ESClient,
    EmbeddingProviderDep,
    PrincipalDep,
    RedisClient,
    RerankerDep,
    get_embedding_breaker,
)
from kb.retrieval.engine import retrieve
from kb.retrieval.models import RetrieveRequest, RetrieveResponse
from kb.security.audit_service import AuditService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieve", tags=["retrieve"])


@router.post("", response_model=RetrieveResponse)
async def retrieve_documents(
    request_body: RetrieveRequest,
    es: ESClient,
    embedding: EmbeddingProviderDep,
    reranker: RerankerDep,
    redis: RedisClient,
    db: DbSession,
    principal: PrincipalDep,
    request: Request,
):
    """混合检索

    ES 原生 RRF (BM25+IK ‖ kNN) → Reranker 精排 → 合规过滤 → 缓存

    I1-C2: principal 注入到 request
      - request.tenant_id 缺省时用 principal.tenant_id (多租户隔离)
      - request.actor_roles 缺省时用 principal.roles (角色匹配)

    I1-C4: 写 KbRetrievalAudit (actor/tenant/query_hash/result_count/latency/degraded)
    """
    if es is None:
        return RetrieveResponse(results=[], total_candidates=0, latency_ms=0)

    # P0-1: 严格 override 防御 — 身份是唯一真相源
    # 客户端不能伪造 tenant_id 跨租户检索, 任何不一致直接 403
    from fastapi import HTTPException

    if request_body.tenant_id is not None and request_body.tenant_id != principal.tenant_id:
        raise HTTPException(
            status_code=403,
            detail="tenant_id 不匹配: 请求体 tenant_id 必须等于当前身份 tenant_id",
        )
    # 强制覆盖: tenant_id 与 actor_roles 一律以 principal 为准
    request_body.tenant_id = principal.tenant_id
    request_body.actor_roles = list(principal.roles)

    # I2-C2: 注入嵌入熔断器 (后台探测, 不可用时跳过 embed → 走 bm25)
    embedding_breaker = get_embedding_breaker(request)

    response = await retrieve(
        request=request_body,
        es_client=es,
        embedding_provider=embedding,
        reranker=reranker,
        redis_client=redis,
        embedding_breaker=embedding_breaker,
    )

    # I2-C4: 嵌入漂移监控 — 每次 retrieve 记录 query embedding, 触发指标
    try:
        from kb.middleware.prometheus import (
            EMBEDDING_DRIFT_DETECTED,
            EMBEDDING_DRIFT_SCORE,
        )
        from kb.retrieval.drift import DriftMonitor

        monitor: DriftMonitor | None = getattr(request.app.state, "drift_monitor", None)
        if monitor and embedding is not None:
            # 取得 query embedding (有缓存时也再算一次, 计入漂移)
            try:
                q_emb = await embedding.embed_query(request_body.query)
                if q_emb:
                    monitor.add(q_emb)
                    signal = monitor.compute_drift()
                    EMBEDDING_DRIFT_SCORE.observe(signal.drift_score)
                    if signal.drift_score >= 0.3:
                        EMBEDDING_DRIFT_DETECTED.labels(severity="critical").inc()
                        logger.warning(
                            "embedding_drift_critical",
                            drift_score=signal.drift_score,
                            sample_size=signal.sample_size,
                        )
                    elif signal.drift_score >= 0.15:
                        EMBEDDING_DRIFT_DETECTED.labels(severity="warn").inc()
            except Exception:
                logger.debug("drift monitor embed_query 失败, 跳过")
    except Exception:
        logger.debug("drift monitor 集成异常, 不影响检索响应")

    # I1-C4: 业务审计 (检索事件)
    try:
        audit = AuditService(db)
        request_id = getattr(request.state, "request_id", None)
        await audit.log_retrieval(
            principal=principal,
            query=request_body.query,
            top_k=request_body.top_k,
            result_count=response.total_candidates,
            latency_ms=response.latency_ms,
            search_type=request_body.search_type,
            degraded=response.degraded,
            request_id=request_id,
        )
        await db.commit()
    except Exception:
        # 审计失败不阻塞响应, 中间件层已记 ERROR
        logger.exception("检索审计落表异常")

    return response
