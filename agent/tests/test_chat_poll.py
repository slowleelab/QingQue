from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from smartcs.services.bot.router import router


def _make_app() -> FastAPI:
    """创建一个带有 mock Redis 的测试应用"""
    app = FastAPI()
    mock_redis = AsyncMock()
    mock_redis.lpush.return_value = 1
    mock_redis.get.return_value = None
    mock_redis.delete.return_value = 0
    app.state.redis_client = mock_redis
    app.include_router(router, prefix="/api")
    return app


@pytest.fixture
async def bot_client():
    """测试客户端，使用独立 FastAPI 应用（绕过完整 lifespan）"""
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_chat_send_returns_accepted(bot_client):
    resp = await bot_client.post("/api/chat/send", json={"message": "你好", "session_id": "test-001"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert "message_id" in data


@pytest.mark.asyncio
async def test_chat_poll_returns_empty_when_no_data(bot_client):
    resp = await bot_client.get("/api/chat/poll", params={"session_id": "test-noexist", "timeout": 1})
    assert resp.status_code == 200
    assert resp.json()["has_message"] is False
