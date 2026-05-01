"""统一依赖注入工厂

提供 FastAPI Depends 使用的依赖注入函数。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from smartcs.services.common.classifier import IntentClassifier, LLMClassifier, RuleClassifier
from smartcs.services.common.database import get_db
from smartcs.services.common.embedding import (
    EmbeddingCircuitBreaker,
    EmbeddingProvider,
    create_embedding_provider,
)
from smartcs.services.common.llm import LLMClient, LLMCircuitBreaker
from smartcs.services.common.redis_client import get_redis
from smartcs.services.common.reranker import (
    RerankerProvider,
    create_reranker_provider,
)
from smartcs.services.common.session import SessionManager
from smartcs.services.common.transfer import TransferChecker
from smartcs.shared.config import get_settings

_logger = logging.getLogger(__name__)


async def _get_app(request: Request):
    """从 Request 中获取 FastAPI app 实例"""
    return request.app


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话（FastAPI 依赖注入）"""
    async for session in get_db(request.app):
        yield session


def get_redis_client(request: Request) -> Redis:
    """获取 Redis 客户端（FastAPI 依赖注入）"""
    return get_redis(request.app)


# 类型别名，方便在路由中使用
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis_client)]


async def init_embedding(app) -> None:
    """初始化嵌入服务，存储到 app.state"""
    settings = get_settings()
    ollama_base = settings.llm.base_url.replace("/v1", "").rstrip("/")
    provider = create_embedding_provider(
        provider_type=settings.rag.embedding_provider,
        ollama_base_url=ollama_base,
        ollama_model=settings.rag.embedding_model,
        tei_base_url=settings.rag.tei_base_url,
        tei_model=settings.rag.tei_embedding_model,
        dim=settings.rag.embedding_dim,
        batch_size=settings.rag.embedding_batch_size,
        timeout=settings.rag.embedding_timeout,
        max_retries=settings.rag.embedding_max_retries,
    )
    # 启动时维度自检
    try:
        test_vec = await provider.embed(["维度校验"])
        actual_dim = len(test_vec[0])
        if actual_dim != settings.milvus.vector_dim:
            raise RuntimeError(f"嵌入维度不匹配: 模型输出 {actual_dim} 维, Milvus 配置 {settings.milvus.vector_dim} 维")
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"嵌入服务维度自检失败: {e}")

    breaker = EmbeddingCircuitBreaker(provider)
    await breaker.start_probe()
    app.state.embedding_breaker = breaker
    app.state.embedding_provider = provider


async def close_embedding(app) -> None:
    """关闭嵌入服务"""
    breaker: EmbeddingCircuitBreaker | None = getattr(app.state, "embedding_breaker", None)
    if breaker:
        await breaker.stop_probe()


async def init_reranker(app) -> None:
    """初始化重排服务，存储到 app.state"""
    settings = get_settings()
    ollama_base = settings.llm.base_url.replace("/v1", "").rstrip("/")
    provider = create_reranker_provider(
        provider_type=settings.rag.reranker_provider,
        ollama_base_url=ollama_base,
        ollama_model=settings.rag.reranker_model,
        tei_base_url=settings.rag.tei_base_url,
        tei_model=settings.rag.reranker_model,
    )
    app.state.reranker_provider = provider


async def close_reranker(app) -> None:
    """关闭重排服务（无需特殊清理）"""
    pass


async def init_elasticsearch(app) -> None:
    """初始化 Elasticsearch 异步客户端，存储到 app.state"""
    from elasticsearch import AsyncElasticsearch

    settings = get_settings()
    es_kwargs: dict[str, Any] = {"hosts": [settings.elasticsearch.hosts]}
    if settings.elasticsearch.username:
        es_kwargs["basic_auth"] = (settings.elasticsearch.username, settings.elasticsearch.password)
    es_kwargs["verify_certs"] = settings.elasticsearch.verify_certs
    client = AsyncElasticsearch(**es_kwargs)
    try:
        await client.ping()
        app.state.es_client = client
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Elasticsearch 连接失败，将使用降级模式: %s", e)
        app.state.es_client = None


async def close_elasticsearch(app) -> None:
    """关闭 Elasticsearch 客户端"""
    client = getattr(app.state, "es_client", None)
    if client:
        await client.close()
        app.state.es_client = None


async def init_milvus(app) -> None:
    """初始化 Milvus Collection，存储到 app.state"""
    from pymilvus import Collection, connections

    settings = get_settings()
    try:
        connections.connect(
            alias="default",
            host=settings.milvus.host,
            port=settings.milvus.port,
        )
        collection = Collection(settings.milvus.collection_name)
        collection.load()
        app.state.milvus_collection = collection
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Milvus 连接失败，将使用降级模式: %s", e)
        app.state.milvus_collection = None


async def close_milvus(app) -> None:
    """关闭 Milvus 连接"""
    from pymilvus import connections

    collection = getattr(app.state, "milvus_collection", None)
    if collection:
        try:
            connections.disconnect("default")
        except Exception:
            pass
        app.state.milvus_collection = None


async def init_minio(app) -> None:
    """初始化 MinIO 客户端，存储到 app.state"""
    from minio import Minio

    settings = get_settings()
    try:
        client = Minio(
            endpoint=settings.minio.endpoint,
            access_key=settings.minio.access_key,
            secret_key=settings.minio.secret_key,
            secure=settings.minio.secure,
        )
        if not client.bucket_exists(settings.minio.bucket):
            client.make_bucket(settings.minio.bucket)
        app.state.minio_client = client
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("MinIO 连接失败: %s", e)
        app.state.minio_client = None


async def close_minio(app) -> None:
    """关闭 MinIO 客户端（无需特殊清理）"""
    app.state.minio_client = None


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    """获取嵌入服务（FastAPI 依赖注入）"""
    return request.app.state.embedding_provider


def get_embedding_breaker(request: Request) -> EmbeddingCircuitBreaker:
    """获取嵌入熔断器（FastAPI 依赖注入）"""
    return request.app.state.embedding_breaker


def get_reranker_provider(request: Request) -> RerankerProvider:
    """获取重排服务（FastAPI 依赖注入）"""
    return request.app.state.reranker_provider


def get_es_client(request: Request) -> Any:
    """获取 Elasticsearch 客户端（FastAPI 依赖注入）"""
    return getattr(request.app.state, "es_client", None)


def get_milvus_collection(request: Request) -> Any:
    """获取 Milvus Collection（FastAPI 依赖注入）"""
    return getattr(request.app.state, "milvus_collection", None)


def get_minio_client(request: Request) -> Any:
    """获取 MinIO 客户端（FastAPI 依赖注入）"""
    return getattr(request.app.state, "minio_client", None)


# ── LLM 客户端 ──


async def init_llm(app) -> None:
    """初始化 LLM 客户端，存储到 app.state"""
    settings = get_settings()
    breaker = LLMCircuitBreaker()
    client = LLMClient(settings=settings.llm, breaker=breaker)
    app.state.llm_client = client
    app.state.llm_breaker = breaker


async def close_llm(app) -> None:
    """关闭 LLM 客户端（无需特殊清理）"""
    app.state.llm_client = None


def get_llm_client(request: Request) -> LLMClient:
    """获取 LLM 客户端（FastAPI 依赖注入）"""
    return request.app.state.llm_client


# ── 会话管理 ──


async def init_session_manager(app) -> None:
    """初始化会话管理器，存储到 app.state"""
    redis = get_redis(app)
    app.state.session_manager = SessionManager(redis)


async def close_session_manager(app) -> None:
    """关闭会话管理器（无需特殊清理）"""
    app.state.session_manager = None


def get_session_manager(request: Request) -> SessionManager:
    """获取会话管理器（FastAPI 依赖注入）"""
    return request.app.state.session_manager


# ── 分类器 ──


async def init_classifier(app) -> None:
    """初始化意图分类器，存储到 app.state"""
    llm_client: LLMClient = app.state.llm_client
    rule_classifier = RuleClassifier()
    llm_classifier = LLMClassifier(llm_client)
    settings = get_settings()
    classifier = IntentClassifier(
        rule_classifier=rule_classifier,
        llm_classifier=llm_classifier,
        fast_threshold=settings.classification.intent_threshold + 0.1,
    )
    app.state.classifier = classifier


async def close_classifier(app) -> None:
    """关闭分类器（无需特殊清理）"""
    app.state.classifier = None


def get_classifier(request: Request) -> IntentClassifier:
    """获取意图分类器（FastAPI 依赖注入）"""
    return request.app.state.classifier


# ── 转人工检查 ──


async def init_transfer_checker(app) -> None:
    """初始化转人工检查器，存储到 app.state"""
    app.state.transfer_checker = TransferChecker()


def get_transfer_checker(request: Request) -> TransferChecker:
    """获取转人工检查器（FastAPI 依赖注入）"""
    return request.app.state.transfer_checker


# ── Agent ──


async def init_agent(app) -> None:
    """初始化对话 Agent，存储到 app.state"""
    from smartcs.services.bot.agent import SmartCSAgent

    classifier: IntentClassifier = app.state.classifier
    llm_client: LLMClient = app.state.llm_client
    transfer_checker: TransferChecker = app.state.transfer_checker
    session_manager: SessionManager = app.state.session_manager

    es_client = getattr(app.state, "es_client", None)
    milvus_collection = getattr(app.state, "milvus_collection", None)
    embedding_breaker = getattr(app.state, "embedding_breaker", None)
    reranker_provider = getattr(app.state, "reranker_provider", None)

    agent = SmartCSAgent(
        classifier=classifier,
        llm_client=llm_client,
        transfer_checker=transfer_checker,
        session_manager=session_manager,
        es_client=es_client,
        milvus_collection=milvus_collection,
        embedding_breaker=embedding_breaker,
        reranker=reranker_provider,
    )
    app.state.agent = agent
    _logger.info("对话 Agent 初始化完成")


async def close_agent(app) -> None:
    """关闭 Agent（无需特殊清理）"""
    app.state.agent = None


def get_agent(request: Request) -> Any:
    """获取对话 Agent（FastAPI 依赖注入）"""
    return request.app.state.agent


# ── 类型别名 ──

EmbeddingProviderDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
EmbeddingBreakerDep = Annotated[EmbeddingCircuitBreaker, Depends(get_embedding_breaker)]
RerankerProviderDep = Annotated[RerankerProvider, Depends(get_reranker_provider)]
ESClientDep = Annotated[Any, Depends(get_es_client)]
MilvusCollectionDep = Annotated[Any, Depends(get_milvus_collection)]
MinioClientDep = Annotated[Any, Depends(get_minio_client)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
SessionManagerDep = Annotated[SessionManager, Depends(get_session_manager)]
ClassifierDep = Annotated[IntentClassifier, Depends(get_classifier)]
TransferCheckerDep = Annotated[TransferChecker, Depends(get_transfer_checker)]
AgentDep = Annotated[Any, Depends(get_agent)]
