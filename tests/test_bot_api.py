"""机器人服务 API 测试"""

from __future__ import annotations

from httpx import AsyncClient


async def test_bot_health_check(bot_client: AsyncClient):
    """测试机器人服务健康检查"""
    response = await bot_client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "bot"


async def test_bot_chat(bot_client: AsyncClient):
    """测试机器人聊天接口"""
    response = await bot_client.post(
        "/api/chat",
        json={"message": "你好"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert "reply" in data
