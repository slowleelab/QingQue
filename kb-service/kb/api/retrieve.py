"""检索 API

POST /retrieve: 混合检索 (ES 原生 RRF + Reranker)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from kb.api.deps import (
    ESClient,
    EmbeddingProviderDep,
    PrincipalDep,
    RedisClient,
    RerankerDep,
)
from kb.retrieval.engine import retrieve
from kb.retrieval.models import RetrieveRequest, RetrieveResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieve", tags=["retrieve"])


@router.post("", response_model=RetrieveResponse)
async def retrieve_documents(
    request: RetrieveRequest,
    es: ESClient,
    embedding: EmbeddingProviderDep,
    reranker: RerankerDep,
    redis: RedisClient,
    principal: PrincipalDep,
):
    """混合检索

    ES 原生 RRF (BM25+IK ‖ kNN) → Reranker 精排 → 合规过滤 → 缓存

    I1-C2: principal 注入到 request
      - request.tenant_id 缺省时用 principal.tenant_id (多租户隔离)
      - request.actor_roles 缺省时用 principal.roles (角色匹配)
    """
    if es is None:
        return RetrieveResponse(results=[], total_candidates=0, latency_ms=0)

    # 自动从 principal 注入多租户/角色上下文 (业务侧未显式提供时)
    if request.tenant_id is None:
        request.tenant_id = principal.tenant_id
    if not request.actor_roles:
        request.actor_roles = list(principal.roles)

    return await retrieve(
        request=request,
        es_client=es,
        embedding_provider=embedding,
        reranker=reranker,
        redis_client=redis,
    )
