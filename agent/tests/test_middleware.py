"""全局异常处理器测试"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from smartcs.shared.exceptions import (
    IntentUnrecognizedError,
    KnowledgeMissError,
    LLMTimeoutError,
    SessionCorruptedError,
    SmartCSError,
)
from smartcs.shared.middleware import register_exception_handlers


def _create_test_app() -> FastAPI:
    """创建带异常处理器的测试 app"""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-smartcs/{error_code}")
    async def raise_smartcs(error_code: int):
        error_classes = {
            2001: IntentUnrecognizedError,
            3001: KnowledgeMissError,
            4001: LLMTimeoutError,
            5001: SessionCorruptedError,
        }
        cls = error_classes.get(error_code, SmartCSError)
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


async def test_smartcs_error_2xxx_returns_400(client: AsyncClient):
    """输入错误 (2xxx) 映射为 HTTP 400"""
    resp = await client.get("/raise-smartcs/2001")
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == 2001
    assert data["error"]["type"] == "IntentUnrecognizedError"


async def test_smartcs_error_3xxx_returns_422(client: AsyncClient):
    """业务错误 (3xxx) 映射为 HTTP 422"""
    resp = await client.get("/raise-smartcs/3001")
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["code"] == 3001


async def test_smartcs_error_4xxx_returns_502(client: AsyncClient):
    """外部依赖错误 (4xxx) 映射为 HTTP 502"""
    resp = await client.get("/raise-smartcs/4001")
    assert resp.status_code == 502
    data = resp.json()
    assert data["error"]["code"] == 4001


async def test_smartcs_error_5xxx_returns_500(client: AsyncClient):
    """系统错误 (5xxx) 映射为 HTTP 500"""
    resp = await client.get("/raise-smartcs/5001")
    assert resp.status_code == 500
    data = resp.json()
    assert data["error"]["code"] == 5001


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
    resp = await client.get("/raise-smartcs/2001")
    data = resp.json()
    assert set(data["error"].keys()) >= {"code", "message", "type"}


async def test_generic_error_production_hides_type():
    """生产环境下不暴露内部异常类型"""
    import os

    os.environ["SMARTCS_ENVIRONMENT"] = "production"
    try:
        # 清除 lru_cache 以读取新的环境变量
        from smartcs.shared.config import get_settings
        get_settings.cache_clear()

        app = _create_test_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/raise-generic")
            data = resp.json()
            assert data["error"]["type"] == "InternalError"
    finally:
        os.environ.pop("SMARTCS_ENVIRONMENT", None)
        # 恢复 lru_cache
        from smartcs.shared.config import get_settings
        get_settings.cache_clear()
