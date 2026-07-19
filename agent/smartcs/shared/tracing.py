"""OpenTelemetry 全链路追踪 — 非侵入式装饰器

业务代码无需 import opentelemetry, 只需加 @traced 装饰器。
追踪未启用时装饰器为零开销空操作。
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable

logger = logging.getLogger(__name__)

_TRACING_ENABLED = os.getenv("SMARTCS_TRACING_ENABLED", "true").lower() == "true"
_provider_initialized = False
_instrumented = False


def _init_tracing() -> None:
    """初始化全局 TracerProvider（只执行一次）"""
    global _provider_initialized
    if not _TRACING_ENABLED or _provider_initialized:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "smartcs"}))
        jaeger_host = os.getenv("JAEGER_HOST", "localhost")
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"http://{jaeger_host}:4318/v1/traces"))
        )
        trace.set_tracer_provider(provider)
        _provider_initialized = True
        logger.info("✅ OpenTelemetry → Jaeger %s:4318", jaeger_host)
    except ImportError:
        logger.debug("opentelemetry 未安装")
    except Exception as e:
        logger.warning("追踪初始化失败: %s", e)


def instrument_app(app, app_name: str) -> None:
    """安装 FastAPI + Redis 自动探针（只执行一次）"""
    global _instrumented
    if not _TRACING_ENABLED or _instrumented:
        return

    _init_tracing()
    _instrumented = True

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        try:
            RedisInstrumentor().instrument()
        except Exception:
            pass
        logger.info("✅ FastAPI + Redis 探针已安装: %s", app_name)
    except Exception as e:
        logger.debug("探针安装跳过: %s", e)


# ── 业务代码使用的装饰器 ──


def traced(name: str | None = None, attrs_fn: Callable | None = None):
    """异步函数追踪装饰器。

    用法:
        @traced("Agent.run")
        async def run_agent(...): ...

        @traced("Worker.消息处理", attrs_fn=lambda sid, msg, **kw: {"session_id": sid})
        async def process(sid, msg): ...

    追踪未启用时为零开销空操作。
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not _TRACING_ENABLED:
                return await func(*args, **kwargs)

            try:
                from opentelemetry import trace as otel_trace
            except ImportError:
                return await func(*args, **kwargs)

            span_name = name or func.__name__
            tracer = otel_trace.get_tracer("smartcs")

            with tracer.start_as_current_span(span_name) as span:
                if attrs_fn:
                    try:
                        attrs = attrs_fn(*args, **kwargs)
                        if isinstance(attrs, dict):
                            for k, v in attrs.items():
                                span.set_attribute(k, v)
                    except Exception:
                        pass
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    span.set_attribute("error", True)
                    raise

        return wrapper

    return decorator


def get_trace_id() -> str:
    """获取当前 trace_id，用于日志关联"""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            return format(span.get_span_context().trace_id, "032x")
    except Exception:
        pass
    return "no-trace"
