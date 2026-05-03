"""pytest 配置和公共 fixtures"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from smartcs.main import assist_app, bot_app


@pytest_asyncio.fixture
async def bot_client() -> AsyncGenerator[AsyncClient, None]:
    """机器人服务测试客户端"""
    transport = ASGITransport(app=bot_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def assist_client() -> AsyncGenerator[AsyncClient, None]:
    """坐席辅助服务测试客户端"""
    transport = ASGITransport(app=assist_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
