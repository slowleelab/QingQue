"""机器人服务 FastAPI 应用"""

from __future__ import annotations

from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from smartcs.services.bot.router import router
from smartcs.shared.config import get_settings
from smartcs.shared.metrics import PrometheusMiddleware, metrics_endpoint


def create_bot_app(lifespan: Callable | None = None) -> FastAPI:
    """创建机器人服务 FastAPI 实例"""
    app = FastAPI(
        title="SmartCS 机器人服务",
        description="银行信用卡智能客服 - 机器人自助问答服务",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Prometheus 指标中间件
    app.add_middleware(PrometheusMiddleware)
    app.add_route("/metrics", metrics_endpoint)

    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    app.include_router(router, prefix="/api")
    return app
