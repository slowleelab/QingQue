"""机器人服务 API 测试"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def test_bot_health_check(bot_client: AsyncClient):
    """测试机器人服务健康检查"""
    response = await bot_client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "bot"


@pytest.mark.asyncio
async def test_bot_chat_requires_agent():
    """测试机器人聊天接口需要 Agent 初始化

    /api/chat 依赖 Agent 和 SessionManager，在完整集成测试环境中运行。
    单元测试通过 test_agent.py 验证 Agent 逻辑。
    """
    # 此测试在缺少外部服务（Redis、ES、Milvus 等）时跳过
    pytest.skip("chat 接口需要完整中间件环境，请通过集成测试验证")
