"""FastAPI 应用入口

启动命令：uvicorn app.main:app 或 kp-api

生命周期：
  启动 → 初始化 PG/Redis/ES/MinIO/Kafka producer/嵌入服务/Reranker
  关闭 → 优雅关闭所有连接

中间件链（外→内）：
  CORS → 审计日志 → 限流 → Prometheus 指标 → 路由
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.documents import router as documents_router
from app.api.retrieve import router as retrieve_router
from app.config import get_settings
from app.database import close_engine
from app.logging import configure_logging, get_logger
from app.middleware.health import router as health_router
from app.middleware.prometheus import PrometheusMiddleware, metrics_endpoint
from app.middleware.rate_limit import RateLimitMiddleware
from app.pipeline.embedder import EmbeddingCircuitBreaker, create_embedding_provider
from app.pipeline.extractor import LLMExtractor
from app.retrieval.reranker import create_reranker_provider
from app.security.audit import AuditMiddleware
from app.storage.elasticsearch import close_es, init_es
from app.storage.kafka import close_producer, init_producer
from app.storage.minio import init_minio
from app.storage.redis import close_redis, init_redis

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("启动 Knowledge Platform API...")

    # 初始化基础设施
    es_client = await init_es()
    await init_redis()
    init_minio()
    await init_producer()

    # 初始化嵌入服务
    ollama_base = settings.llm.base_url.replace("/v1", "").rstrip("/")
    provider = create_embedding_provider(
        provider_type=settings.rag.embedding_provider,
        ollama_base_url=ollama_base,
        ollama_model=settings.rag.embedding_model,
        tei_base_url=settings.rag.tei_base_url,
        tei_model=settings.rag.embedding_model,
        dim=settings.rag.embedding_dim,
        batch_size=settings.rag.embedding_batch_size,
        timeout=settings.rag.embedding_timeout,
    )
    breaker = EmbeddingCircuitBreaker(provider)
    await breaker.start_probe()
    app.state.embedding_provider = provider
    app.state.embedding_breaker = breaker

    # Reranker
    reranker = create_reranker_provider(
        provider_type=settings.rag.reranker_provider,
        tei_base_url=settings.rag.tei_rerank_base_url,
        tei_model=settings.rag.reranker_model,
        ollama_base_url=ollama_base,
        ollama_model=settings.rag.reranker_model,
    )
    app.state.reranker_provider = reranker

    # LLM 抽取器
    app.state.llm_extractor = LLMExtractor()

    app.state.es_client = es_client
    logger.info("Knowledge Platform API 启动完成")

    yield

    # 优雅关闭
    logger.info("正在关闭...")
    await breaker.stop_probe()
    await close_producer()
    await close_es()
    await close_redis()
    await close_engine()
    logger.info("已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    settings = get_settings()
    app = FastAPI(
        title="Knowledge Platform API",
        description="银行知识数据平台 — 离线ETL + 在线混合检索",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # 中间件链（注册顺序与执行顺序相反，最后注册的最先执行）
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    app.add_middleware(AuditMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=settings.security.rate_limit_per_minute,
    )
    app.add_middleware(PrometheusMiddleware)

    # 路由
    app.include_router(health_router)
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(retrieve_router, prefix="/api/v1")

    # Prometheus 指标端点
    app.add_route("/metrics", metrics_endpoint, methods=["GET"])

    return app


app = create_app()


def main() -> None:
    """API 服务入口"""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.service_host,
        port=settings.service_port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
