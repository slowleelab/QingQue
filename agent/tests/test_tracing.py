"""tracing.py 补充测试 — 补齐 observability 未覆盖的初始化/异常分支"""

from __future__ import annotations

import sys

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lumio.shared import tracing

# ── _load_tracing_config ──


def test_load_tracing_config_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置可用时返回 (enabled, jaeger_host, otlp_endpoint)"""
    import lumio.shared.config as config_mod

    class _FakeObs:
        tracing_enabled = True
        jaeger_host = "j1"
        otlp_endpoint = None

    monkeypatch.setattr(config_mod, "ObservabilitySettings", lambda: _FakeObs())
    enabled, host, endpoint = tracing._load_tracing_config()
    assert enabled is True
    assert host == "j1"
    assert endpoint is None


def test_load_tracing_config_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings 不可用时保守降级关闭"""
    import lumio.shared.config as config_mod

    def boom() -> None:
        raise RuntimeError("config not ready")

    monkeypatch.setattr(config_mod, "ObservabilitySettings", boom)
    enabled, host, endpoint = tracing._load_tracing_config()
    assert enabled is False
    assert host == "localhost"
    assert endpoint is None


# ── _init_tracing ──


def test_init_tracing_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """tracing 关闭时直接返回, 不初始化 provider"""
    monkeypatch.setattr(tracing, "_provider_initialized", False)
    monkeypatch.setattr(tracing, "_load_tracing_config", lambda: (False, "localhost", None))
    tracing._init_tracing("test-app")
    assert tracing._provider_initialized is False


def test_init_tracing_already_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    """已初始化时跳过 (幂等), 不重复创建 provider"""
    monkeypatch.setattr(tracing, "_provider_initialized", True)
    calls: list[str] = []

    def fake_load() -> tuple[bool, str, str | None]:
        calls.append("load")
        return True, "localhost", None

    monkeypatch.setattr(tracing, "_load_tracing_config", fake_load)
    tracing._init_tracing("test-app")
    # load 总是先执行, 但不会走到 provider 创建 (无异常即幂等)
    assert calls == ["load"]
    assert tracing._provider_initialized is True


def test_init_tracing_otel_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """opentelemetry 未安装时安静跳过"""
    monkeypatch.setattr(tracing, "_provider_initialized", False)
    monkeypatch.setattr(tracing, "_load_tracing_config", lambda: (True, "localhost", None))
    monkeypatch.setitem(sys.modules, "opentelemetry", None)  # type: ignore[assignment]
    tracing._init_tracing("test-app")
    assert tracing._provider_initialized is False


def test_init_tracing_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """启用且未初始化 → 创建 provider 并标记已初始化"""
    monkeypatch.setattr(tracing, "_provider_initialized", False)
    monkeypatch.setattr(tracing, "_load_tracing_config", lambda: (True, "jaeger.local", None))
    saved = otel_trace.get_tracer_provider()
    try:
        tracing._init_tracing("lumio-bot")
        assert tracing._provider_initialized is True
    finally:
        otel_trace._TRACER_PROVIDER = saved  # 还原全局 provider


# ── instrument_app ──


def test_instrument_app_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """tracing 关闭时不安装探针"""
    monkeypatch.setattr(tracing, "_instrumented", False)
    monkeypatch.setattr(tracing, "_load_tracing_config", lambda: (False, "localhost", None))
    tracing.instrument_app(object(), "test")
    assert tracing._instrumented is False


def test_instrument_app_already_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """已安装过探针时跳过 (幂等), 不重复安装"""
    monkeypatch.setattr(tracing, "_instrumented", True)
    calls: list[str] = []

    def fake_load() -> tuple[bool, str, str | None]:
        calls.append("load")
        return True, "localhost", None

    monkeypatch.setattr(tracing, "_load_tracing_config", fake_load)
    tracing.instrument_app(object(), "test")
    # load 总是先执行, 但不会走到探针安装 (无异常即幂等)
    assert calls == ["load"]
    assert tracing._instrumented is True


def test_instrument_app_otel_instrument_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """instrumentation 库缺失时静默跳过 (探针标记已置)"""
    monkeypatch.setattr(tracing, "_instrumented", False)
    monkeypatch.setattr(tracing, "_provider_initialized", False)
    monkeypatch.setattr(tracing, "_load_tracing_config", lambda: (True, "localhost", None))
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.fastapi", None)  # type: ignore[assignment]
    tracing.instrument_app(object(), "test")
    assert tracing._instrumented is True


# ── @traced: attrs_fn / 异常路径 ──


async def test_traced_attrs_fn_sets_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """attrs_fn 返回的 dict 写入 span attribute"""
    monkeypatch.setattr(tracing, "_TRACING_ENABLED", True)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    saved = otel_trace.get_tracer_provider()
    otel_trace._TRACER_PROVIDER = provider
    try:

        @tracing.traced("with_attrs", attrs_fn=lambda sid, **kw: {"session_id": sid})
        async def f(sid: str) -> str:
            return sid

        assert await f("s-123") == "s-123"
    finally:
        otel_trace._TRACER_PROVIDER = saved

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["session_id"] == "s-123"


async def test_traced_attrs_fn_exception_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """attrs_fn 抛异常不影响主函数"""
    monkeypatch.setattr(tracing, "_TRACING_ENABLED", True)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    saved = otel_trace.get_tracer_provider()
    otel_trace._TRACER_PROVIDER = provider
    try:

        def bad_attrs(*args, **kwargs) -> dict[str, str]:
            raise ValueError("bad attrs")

        @tracing.traced("f", attrs_fn=bad_attrs)
        async def f() -> int:
            return 42

        assert await f() == 42
    finally:
        otel_trace._TRACER_PROVIDER = saved


async def test_traced_propagates_exception_and_marks_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """函数异常向上传播且 span 标记 error"""
    monkeypatch.setattr(tracing, "_TRACING_ENABLED", True)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    saved = otel_trace.get_tracer_provider()
    otel_trace._TRACER_PROVIDER = provider
    try:

        @tracing.traced("fails")
        async def f() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await f()
    finally:
        otel_trace._TRACER_PROVIDER = saved

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes.get("error") is True


async def test_traced_otel_import_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """opentelemetry 缺失时 @traced 退化为直接调用"""
    monkeypatch.setattr(tracing, "_TRACING_ENABLED", True)
    monkeypatch.setitem(sys.modules, "opentelemetry", None)  # type: ignore[assignment]

    @tracing.traced("x")
    async def f() -> int:
        return 7

    assert await f() == 7


# ── get_trace_context 异常分支 ──


def test_get_trace_context_otel_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """opentelemetry 缺失时返回 None"""
    monkeypatch.setattr(tracing, "_TRACING_ENABLED", True)
    monkeypatch.setitem(sys.modules, "opentelemetry", None)  # type: ignore[assignment]
    assert tracing.get_trace_context() is None


# ── _read_service_version 兜底分支 ──


def test_service_version_no_project_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """pyproject 无 [project]/[tool.poetry] 表时降级 0.0.0"""
    monkeypatch.delenv("LUMIO_VERSION", raising=False)

    class _FakeTomllib:
        @staticmethod
        def load(f) -> dict:
            return {"other": 1}  # 无 project/poetry 表

    monkeypatch.setitem(sys.modules, "tomllib", _FakeTomllib())
    assert tracing._read_service_version() == "0.0.0"
