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

    # 自动从 principal 注入多租户/角色上下文 (业务侧未显式提供时)
    if request_body.tenant_id is None:
        request_body.tenant_id = principal.tenant_id
    if not request_body.actor_roles:
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
