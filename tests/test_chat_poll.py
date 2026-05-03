"""Bot 长轮询端点测试"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from smartcs.main import bot_app


@pytest.fixture
async def bot_client():
    transport = ASGITransport(app=bot_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_chat_send_endpoint_exists(bot_client):
    """chat/send 端点存在，无 Redis 时返回 503"""
    resp = await bot_client.post("/api/chat/send", json={
        "message": "你好",
        "session_id": "test-send-001",
    })
    # 无 Redis 时返回 503，端点存在即可
    assert resp.status_code in (200, 503)


@pytest.mark.asyncio
async def test_chat_poll_returns_empty_when_no_data(bot_client):
    resp = await bot_client.get("/api/chat/poll", params={
        "session_id": "test-noexist-002",
        "timeout": 1,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_message"] is False


@pytest.mark.asyncio
async def test_health_still_works(bot_client):
    resp = await bot_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
