"""坐席辅助服务 FastAPI 应用"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from smartcs.services.assist.router import router
from smartcs.shared.config import get_settings
from smartcs.shared.metrics import PrometheusMiddleware, metrics_endpoint
from smartcs.shared.rate_limit import create_limiter
from smartcs.shared.tracing import instrument_app


def create_assist_app(lifespan: Callable | None = None) -> FastAPI:
    """创建坐席辅助服务 FastAPI 实例"""
    app = FastAPI(
        title="SmartCS 坐席辅助服务",
        description="银行信用卡智能客服 - 坐席辅助服务。提供 AI 辅助建议、OE 编排推送、会话管理、反馈收集。",
        version="0.2.0",
        lifespan=lifespan,
        contact={"name": "SmartCS", "url": "https://github.com/slowleelab/QingQue"},
        license_info={"name": "Apache 2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
        openapi_tags=[
            {"name": "assist", "description": "坐席辅助 — 消息分析、OE 推送、会话保持/恢复、事后复盘"},
        ],
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # 限流
    limiter = create_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded,
        lambda req, exc: JSONResponse(
            status_code=429,
            content={"error": {"code": 4290, "message": "请求过于频繁，请稍后重试", "type": "RateLimitExceeded"}},
        ),
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
    app.add_middleware(SlowAPIMiddleware)

    app.include_router(router, prefix="/api")

    # 认证与管理路由
    from smartcs.services.common.auth_router import router as auth_router

    app.include_router(auth_router, prefix="/api")
    # FAQ 管理路由
    from smartcs.services.common.faq_router import router as faq_router

    app.include_router(faq_router, prefix="/api")

    # 审计日志中间件
    from smartcs.shared.audit_middleware import register_audit_middleware

    register_audit_middleware(app)

    # OpenTelemetry 全链路追踪
    instrument_app(app, "smartcs-assist")

    return app
