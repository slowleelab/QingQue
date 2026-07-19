"""API 依赖注入"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from elasticsearch import AsyncElasticsearch
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.pipeline.embedder import EmbeddingCircuitBreaker, EmbeddingProvider
from app.retrieval.reranker import RerankerProvider
from app.security.auth import verify_api_key

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


# 类型别名
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis | None, Depends(get_redis_client)]
ESClient = Annotated[AsyncElasticsearch | None, Depends(get_es_client)]
EmbeddingProviderDep = Annotated[EmbeddingProvider | None, Depends(get_embedding_provider)]
RerankerDep = Annotated[RerankerProvider | None, Depends(get_reranker_provider)]
ApiKeyDep = Annotated[str, Depends(verify_api_key)]
