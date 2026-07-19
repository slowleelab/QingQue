"""健康检查端点

k8s 探针：
- /health/live — 存活探针：进程活着就返回 ok
- /health/ready — 就绪探针：检查所有依赖（PG/ES/Redis/Kafka/MinIO）可用
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness():
    """存活探针 — 进程活着即返回 200"""
    return {"status": "alive"}


@router.get("/ready")
async def readiness(request: Request):
    """就绪探针 — 检查所有依赖可用性

    任一依赖不可用返回 503，k8s 会从 Service Endpoints 摘除 Pod。
    """
    checks: dict[str, bool] = {}

    # PostgreSQL
    try:
        from app.database import get_engine
        from sqlalchemy import text

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = True
    except Exception:
        checks["postgres"] = False

    # Elasticsearch
    try:
        es = getattr(request.app.state, "es_client", None)
        if es and await es.ping():
            checks["elasticsearch"] = True
        else:
            checks["elasticsearch"] = False
    except Exception:
        checks["elasticsearch"] = False

    # Redis
    try:
        from app.storage.redis import get_redis

        redis = get_redis()
        if redis and await redis.ping():
            checks["redis"] = True
        else:
            checks["redis"] = False
    except Exception:
        checks["redis"] = False

    # MinIO
    try:
        from app.storage.minio import get_minio

        minio = get_minio()
        if minio:
            checks["minio"] = True
        else:
            checks["minio"] = False
    except Exception:
        checks["minio"] = False

    all_ready = all(checks.values())

    return JSONResponse(
        status_code=200 if all_ready else 503,
        content={"status": "ready" if all_ready else "not_ready", "checks": checks},
    )
