"""全局异常处理器测试"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lumio.shared.exceptions import (
    IntentUnrecognizedError,
    InvalidTransitionError,
    KnowledgeMissError,
    LLMTimeoutError,
    LumioError,
    ServiceOverloadedError,
    SessionCorruptedError,
    SessionNotFoundError,
)
from lumio.shared.middleware import register_exception_handlers


def _create_test_app() -> FastAPI:
    """创建带异常处理器的测试 app"""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-lumio/{error_code}")
    async def raise_lumio(error_code: int):
        error_classes = {
            2001: IntentUnrecognizedError,
            3001: KnowledgeMissError,
            3004: SessionNotFoundError,
            3005: InvalidTransitionError,
            4001: LLMTimeoutError,
            5001: SessionCorruptedError,
            5002: ServiceOverloadedError,
        }
        cls = error_classes.get(error_code, LumioError)
        raise cls()

    @app.get("/raise-generic")
    async def raise_generic():
        raise ValueError("test error")

    return app


@pytest.fixture
def test_app():
    return _create_test_app()


@pytest.fixture
async def client(test_app):
    # raise_app_exceptions=False 让 FastAPI 异常处理器返回 HTTP 响应而非抛出异常
    transport = ASGITransport(app=test_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_lumio_error_2xxx_returns_400(client: AsyncClient):
    """输入错误 (2xxx) 映射为 HTTP 400"""
    resp = await client.get("/raise-lumio/2001")
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == 2001
    assert data["error"]["type"] == "IntentUnrecognizedError"


async def test_lumio_error_3xxx_returns_422(client: AsyncClient):
    """业务错误 (3xxx) 映射为 HTTP 422"""
    resp = await client.get("/raise-lumio/3001")
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["code"] == 3001


async def test_session_not_found_returns_404(client: AsyncClient):
    """会话不存在 (3004) 映射为 HTTP 404"""
    resp = await client.get("/raise-lumio/3004")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"]["code"] == 3004
    assert data["error"]["type"] == "SessionNotFoundError"


async def test_invalid_transition_returns_409(client: AsyncClient):
    """非法状态转换 (3005) 映射为 HTTP 409"""
    resp = await client.get("/raise-lumio/3005")
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"]["code"] == 3005
    assert data["error"]["type"] == "InvalidTransitionError"


async def test_lumio_error_4xxx_returns_502(client: AsyncClient):
    """外部依赖错误 (4xxx) 映射为 HTTP 502"""
    resp = await client.get("/raise-lumio/4001")
    assert resp.status_code == 502
    data = resp.json()
    assert data["error"]["code"] == 4001


async def test_lumio_error_5xxx_returns_500(client: AsyncClient):
    """系统错误 (5xxx) 映射为 HTTP 500"""
    resp = await client.get("/raise-lumio/5001")
    assert resp.status_code == 500
    data = resp.json()
    assert data["error"]["code"] == 5001


async def test_service_overloaded_returns_503(client: AsyncClient):
    """P3-9 整改: ServiceOverloadedError (5002) 显式映射 503 而非默认 500.

    银行客户端 (chat-svc) 看到 503 能识别为'依赖未就绪', 而非'系统 bug'.
    """
    resp = await client.get("/raise-lumio/5002")
    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["error"]["code"] == 5002
    assert data["error"]["type"] == "ServiceOverloadedError"


async def test_generic_error_returns_500(client: AsyncClient):
    """未捕获异常映射为 HTTP 500"""
    resp = await client.get("/raise-generic")
    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["error"]["code"] == 5000
    # 默认环境为 development，暴露异常类型
    assert data["error"]["type"] == "ValueError"


async def test_error_response_format(client: AsyncClient):
    """所有错误响应遵循统一格式 {"error": {"code", "message", "type"}}"""
    resp = await client.get("/raise-lumio/2001")
    data = resp.json()
    assert set(data["error"].keys()) >= {"code", "message", "type"}


async def test_generic_error_production_hides_type():
    """生产环境下不暴露内部异常类型"""
    import os

    os.environ["LUMIO_ENVIRONMENT"] = "production"
    os.environ["LUMIO_JWT_SECRET"] = "x" * 32  # 生产环境必须设置安全密钥
    # P0-5 第三轮修复: 生产环境还要求外部服务凭据非默认
    os.environ["LLM_API_KEY"] = "sk-test-key"
    os.environ["MINIO_ACCESS_KEY"] = "test-access"
    os.environ["MINIO_SECRET_KEY"] = "test-secret"
    os.environ["ES_USERNAME"] = "es-user"
    os.environ["ES_PASSWORD"] = "es-pass"
    os.environ["REDIS_PASSWORD"] = "redis-pass"
    try:
        # 清除 lru_cache 以读取新的环境变量
        from lumio.shared.config import get_settings

        get_settings.cache_clear()

        app = _create_test_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/raise-generic")
            data = resp.json()
            assert data["error"]["type"] == "InternalError"
    finally:
        os.environ.pop("LUMIO_ENVIRONMENT", None)
        os.environ.pop("LUMIO_JWT_SECRET", None)
        for _k in ("LLM_API_KEY", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "ES_USERNAME", "ES_PASSWORD", "REDIS_PASSWORD"):
            os.environ.pop(_k, None)
        # 恢复 lru_cache
        from lumio.shared.config import get_settings

        get_settings.cache_clear()
