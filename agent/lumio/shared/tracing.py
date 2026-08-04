"""OpenTelemetry 全链路追踪 — 非侵入式装饰器

业务代码无需 import opentelemetry, 只需加 @traced 装饰器。
追踪未启用时装饰器为零开销空操作。
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# 启动期从 ObservabilitySettings 读初值。运行期仍可被 monkeypatch 切换 (单测常用).
_TRACING_ENABLED = True
_provider_initialized = False
_instrumented = False


def _read_service_version() -> str:
    """读 service.version, 优先级: LUMIO_VERSION env > pyproject.toml > 0.0.0.

    优先 env 注入 (CI/CD 可控); 缺省从 pyproject.toml [project] table 解析,
    真正'开箱即用'而无需用户配 .env. 最终降级 0.0.0 (OpenTelemetry 通用 fallback).
    """
    env_ver = os.getenv("LUMIO_VERSION")
    if env_ver:
        return env_ver

    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python < 3.11
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return "0.0.0"

    # pyproject.toml 在 agent/ 下, 与本文件 (shared/tracing.py) 同根的父目录
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, KeyError):
        return "0.0.0"
    # 兼容 PEP 621 [project] 与 Poetry 1.x [tool.poetry] 两种格式
    return str(
        data.get("project", {}).get("version")
        or data.get("tool", {}).get("poetry", {}).get("version")
        or "0.0.0"
    )


def _load_tracing_config() -> tuple[bool, str, str | None]:
    """从 ObservabilitySettings 读当前 tracing 配置。

    返回 (enabled, jaeger_host, otlp_endpoint).
    若 Settings 不可用 (导入失败 / 早期启动), 用 (False, 'localhost', None) 保守返回 —
    避免 tracing 提前初始化无后端的环境。
    """
    try:
        from lumio.shared.config import ObservabilitySettings

        cfg = ObservabilitySettings()
        return cfg.tracing_enabled, cfg.jaeger_host, cfg.otlp_endpoint
    except Exception as e:  # 配置未就绪时降级关闭
        logger.debug("ObservabilitySettings 不可用, 默认关闭 tracing: %s", e)
        return False, "localhost", None


def _init_tracing(app_name: str = "lumio") -> None:
    """初始化全局 TracerProvider（只执行一次）

    Resource attributes (commit 7 补齐, 便于 Jaeger 按 service / env 过滤):
    - service.name: 应用名 (lumio / lumio-bot / lumio-assist)
    - service.namespace: 业务域, 固定 lumio
    - service.version: 读 pyproject.toml 或 LUMIO_VERSION env
    - deployment.environment: 从 Settings.environment 读
    """
    global _provider_initialized
    enabled, jaeger_host, otlp_endpoint = _load_tracing_config()
    if not enabled or _provider_initialized:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatioSampler

        # 读 deployment.environment (Settings 可能未就绪, 降级 'unknown')
        try:
            from lumio.shared.config import get_settings

            deployment_env = get_settings().environment
            sampling_ratio = get_settings().observability.sampling_ratio
        except Exception:
            deployment_env = "unknown"
            sampling_ratio = 1.0

        # 读 service.version (LUMIO_VERSION env > pyproject.toml > 0.0.0).
        service_version = _read_service_version()

        resource = Resource.create(
            {
                "service.name": app_name,
                "service.namespace": "lumio",
                "service.version": service_version,
                "deployment.environment": deployment_env,
            }
        )
        # ParentBased 包装: 当上游有 traceparent 时跟随上游决策, 否则按 ratio 采样.
        # 这样跨服务 trace 不会被本地采样率切断.
        sampler = ParentBasedTraceIdRatioSampler(sampling_ratio)
        provider = TracerProvider(resource=resource, sampler=sampler)
        endpoint = otlp_endpoint or f"http://{jaeger_host}:4318/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        _provider_initialized = True
        logger.info(
            "✅ OpenTelemetry → %s (service=%s, env=%s, version=%s, sample=%.2f)",
            endpoint,
            app_name,
            deployment_env,
            service_version,
            sampling_ratio,
        )
    except ImportError:
        logger.debug("opentelemetry 未安装")
    except Exception as e:
        logger.warning("追踪初始化失败: %s", e)


def instrument_app(app, app_name: str) -> None:
    """安装 FastAPI + Redis 自动探针（只执行一次）"""
    global _instrumented
    enabled, _, _ = _load_tracing_config()
    if not enabled or _instrumented:
        return

    _init_tracing(app_name=app_name)
    _instrumented = True

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        with contextlib.suppress(Exception):
            RedisInstrumentor().instrument()
        # HTTPX 探针：MCP streamable-http 每次出站 POST 自动注入 W3C traceparent，
        # 使 Python 客户端 span 与下游 Java server span 串成同一条链路。
        with contextlib.suppress(Exception):
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
        logger.info("✅ FastAPI + Redis + HTTPX 探针已安装: %s", app_name)
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
            tracer = otel_trace.get_tracer("lumio")

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


def get_trace_context() -> tuple[str, str] | None:
    """获取当前活跃 span 的 (trace_id, span_id)（十六进制）。

    无活跃 span、tracing 未启用或依赖缺失时返回 ``None``——
    调用方据此省略日志中的追踪字段，实现零开销/零噪声。
    """
    if not _TRACING_ENABLED:
        return None
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:
        pass
    return None
