"""坐席辅助服务 API 测试"""

from __future__ import annotations

from httpx import AsyncClient


async def test_assist_health_check(assist_client: AsyncClient):
    """测试坐席辅助服务健康检查"""
    response = await assist_client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "assist"
