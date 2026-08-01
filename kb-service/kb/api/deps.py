"""API 依赖注入"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from elasticsearch import AsyncElasticsearch
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from kb.database import get_db
from kb.pipeline.embedder import EmbeddingCircuitBreaker, EmbeddingProvider
from kb.retrieval.reranker import RerankerProvider
from kb.security.auth import Principal, verify_principal

logger = logging.getLogger(__name__)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db():
        yield session


def get_redis_client(request: Request) -> Redis | None:
    return getattr(request.app.state, "redis_client", None)


def get_es_client(request: Request) -> AsyncElasticsearch | None:
    return getattr(request.app.state, "es_client", None)


def get_embedding_provider(request: Request) -> EmbeddingProvider | None:
    return getattr(request.app.state, "embedding_provider", None)


def get_embedding_breaker(request: Request) -> EmbeddingCircuitBreaker | None:
    return getattr(request.app.state, "embedding_breaker", None)


def get_reranker_provider(request: Request) -> RerankerProvider | None:
    return getattr(request.app.state, "reranker_provider", None)


def get_llm_extractor(request: Request):
    return getattr(request.app.state, "llm_extractor", None)


def get_principal_from_request(request: Request) -> Principal:
    """从 request.state 取 principal (由 verify_principal 注入)

    给非认证端点 (health/metrics) 或服务端内部调用使用.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None:
        # 兜底: 给 health/metrics 端点用匿名身份
        from kb.config import get_settings

        settings = get_settings()
        return Principal(
            actor_id="system",
            actor_role="service",
            tenant_id=settings.security.default_tenant_id,
            roles=["service"],
            tier="internal",
            auth_method="api_key",
        )
    return principal


# 类型别名 (I1-C2: ApiKeyDep 改名为 PrincipalDep, 但保留向后兼容别名)
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis | None, Depends(get_redis_client)]
ESClient = Annotated[AsyncElasticsearch | None, Depends(get_es_client)]
EmbeddingProviderDep = Annotated[EmbeddingProvider | None, Depends(get_embedding_provider)]
RerankerDep = Annotated[RerankerProvider | None, Depends(get_reranker_provider)]
PrincipalDep = Annotated[Principal, Depends(verify_principal)]

# 向后兼容: 旧代码用的 ApiKeyDep 别名 (I1-C2 期间过渡)
ApiKeyDep = PrincipalDep
