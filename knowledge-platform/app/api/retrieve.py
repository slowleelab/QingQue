"""检索 API

POST /retrieve: 混合检索 (ES 原生 RRF + Reranker)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.api.deps import (
    ApiKeyDep,
    EmbeddingProviderDep,
    ESClient,
    RedisClient,
    RerankerDep,
)
from app.retrieval.engine import retrieve
from app.retrieval.models import RetrieveResponse, RetrieveRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieve", tags=["retrieve"])


@router.post("", response_model=RetrieveResponse)
async def retrieve_documents(
    request: RetrieveRequest,
    es: ESClient,
    embedding: EmbeddingProviderDep,
    reranker: RerankerDep,
    redis: RedisClient,
    _api_key: ApiKeyDep,
):
    """混合检索

    ES 原生 RRF (BM25+IK ‖ kNN) → Reranker 精排 → 合规过滤 → 缓存
    """
    if es is None:
        return RetrieveResponse(results=[], total_candidates=0, latency_ms=0)

    return await retrieve(
        request=request,
        es_client=es,
        embedding_provider=embedding,
        reranker=reranker,
        redis_client=redis,
    )
