"""健康检查工具

提供依赖组件连通性检查，用于 readiness/liveness 探针。

- /api/health/live — liveness：进程存活即 200
- /api/health/ready — readiness：检查 Redis/DB/ES/Milvus 等依赖
- /api/health — 兼容端点，返回详细状态
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _check_redis(app: Any) -> dict[str, Any]:
    """检查 Redis 连通性"""
    redis = getattr(app.state, "redis_client", None)
    if redis is None:
        return {"status": "skip", "reason": "not_initialized"}
    try:
        await asyncio.wait_for(redis.ping(), timeout=2.0)
        return {"status": "up"}
    except Exception as e:
        return {"status": "down", "error": str(e)[:100]}


async def _check_db(app: Any) -> dict[str, Any]:
    """检查 PostgreSQL 连通性"""
    engine = getattr(app.state, "db_engine", None)
    if engine is None:
        return {"status": "skip", "reason": "not_initialized"}
    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=3.0)
        return {"status": "up"}
    except Exception as e:
        return {"status": "down", "error": str(e)[:100]}


async def _check_es(app: Any) -> dict[str, Any]:
    """检查 Elasticsearch 连通性"""
    es = getattr(app.state, "es_client", None)
    if es is None:
        return {"status": "skip", "reason": "not_initialized"}
    try:
        await asyncio.wait_for(es.info(), timeout=3.0)
        return {"status": "up"}
    except Exception as e:
        return {"status": "down", "error": str(e)[:100]}


async def _check_llm(app: Any) -> dict[str, Any]:
    """检查 LLM 服务连通性"""
    llm = getattr(app.state, "llm_client", None)
    if llm is None:
        return {"status": "skip", "reason": "not_initialized"}
    try:
        ok = await asyncio.wait_for(llm.health_check(), timeout=5.0)
        return {"status": "up" if ok else "down"}
    except Exception as e:
        return {"status": "down", "error": str(e)[:100]}


async def _check_embedding(app: Any) -> dict[str, Any]:
    """检查 Embedding 服务连通性"""
    breaker = getattr(app.state, "embedding_breaker", None)
    if breaker is None:
        return {"status": "skip", "reason": "not_initialized"}
    return {"status": "up" if breaker.is_available else "down"}


async def check_all_dependencies(app: Any) -> dict[str, dict[str, Any]]:
    """并行检查所有依赖组件"""
    results = await asyncio.gather(
        _check_redis(app),
        _check_db(app),
        _check_es(app),
        _check_llm(app),
        _check_embedding(app),
    )
    return {
        "redis": results[0],
        "postgres": results[1],
        "elasticsearch": results[2],
        "llm": results[3],
        "embedding": results[4],
    }


def aggregate_health(deps: dict[str, dict[str, Any]]) -> tuple[str, int]:
    """汇总依赖状态，返回 (overall_status, http_code)

    - 所有 up/skip → healthy, 200
    - 有 down 但核心依赖(redis/db) up → degraded, 200
    - 核心依赖 down → unhealthy, 503
    """
    core_deps = ["redis", "postgres"]
    all_ok = True
    core_ok = True

    for name, status in deps.items():
        if status["status"] == "down":
            all_ok = False
            if name in core_deps:
                core_ok = False

    if all_ok:
        return "healthy", 200
    if core_ok:
        return "degraded", 200
    return "unhealthy", 503
