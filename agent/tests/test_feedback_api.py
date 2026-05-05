"""隐式反馈端点测试

测试 POST /api/feedback 端点和 _action_to_confidence 映射。
使用 httpx.AsyncClient + FastAPI TestClient 模式，无需启动真实服务器。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from smartcs.main import create_assist_app
from smartcs.services.assist.router import _action_to_confidence


# ── Unit tests: _action_to_confidence ──


@pytest.mark.parametrize(
    "action, expected",
    [
        ("accept", 1.0),
        ("modify", 0.5),
        ("partial_accept", 0.3),
        ("reject", 0.0),
    ],
)
def test_action_to_confidence_known_actions(action: str, expected: float):
    """已知操作类型返回正确置信度"""
    assert _action_to_confidence(action) == expected


def test_action_to_confidence_unknown_action():
    """未知操作类型返回 0.0"""
    assert _action_to_confidence("unknown") == 0.0
    assert _action_to_confidence("") == 0.0


# ── Integration tests: POST /api/feedback ──


@pytest_asyncio.fixture
async def feedback_client():
    """创建测试用 AsyncClient，注入 mock 依赖"""
    app = create_assist_app()

    # 注入空 classifier + orchestrator 避免启动依赖
    app.state.classifier = None
    app.state.assist_orchestrator = None
    app.state.temporal_client = None
    app.state.state_manager = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_feedback_accept(feedback_client: AsyncClient):
    """POST /api/feedback accept 操作返回 confidence 1.0"""
    resp = await feedback_client.post("/api/feedback", json={
        "session_id": "test-session-1",
        "agent_id": "agent-001",
        "action": "accept",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["action"] == "accept"
    assert data["confidence"] == 1.0


async def test_feedback_modify(feedback_client: AsyncClient):
    """POST /api/feedback modify 操作返回 confidence 0.5"""
    resp = await feedback_client.post("/api/feedback", json={
        "session_id": "test-session-2",
        "agent_id": "agent-001",
        "action": "modify",
        "modify_fields": ["script_content"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "modify"
    assert data["confidence"] == 0.5


async def test_feedback_partial_accept(feedback_client: AsyncClient):
    """POST /api/feedback partial_accept 操作返回 confidence 0.3"""
    resp = await feedback_client.post("/api/feedback", json={
        "session_id": "test-session-3",
        "agent_id": "agent-001",
        "action": "partial_accept",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "partial_accept"
    assert data["confidence"] == 0.3


async def test_feedback_reject(feedback_client: AsyncClient):
    """POST /api/feedback reject 操作返回 confidence 0.0"""
    resp = await feedback_client.post("/api/feedback", json={
        "session_id": "test-session-4",
        "agent_id": "agent-001",
        "action": "reject",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "reject"
    assert data["confidence"] == 0.0


async def test_feedback_default_action(feedback_client: AsyncClient):
    """POST /api/feedback 不传 action 默认为 reject"""
    resp = await feedback_client.post("/api/feedback", json={
        "session_id": "test-session-5",
        "agent_id": "agent-001",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "reject"
    assert data["confidence"] == 0.0


async def test_feedback_missing_session_id(feedback_client: AsyncClient):
    """POST /api/feedback 缺少 session_id 返回 422"""
    resp = await feedback_client.post("/api/feedback", json={
        "agent_id": "agent-001",
        "action": "accept",
    })
    assert resp.status_code == 422


async def test_feedback_missing_agent_id(feedback_client: AsyncClient):
    """POST /api/feedback 缺少 agent_id 返回 422"""
    resp = await feedback_client.post("/api/feedback", json={
        "session_id": "test-session",
        "action": "accept",
    })
    assert resp.status_code == 422


async def test_feedback_invalid_action(feedback_client: AsyncClient):
    """POST /api/feedback 无效 action 返回 422"""
    resp = await feedback_client.post("/api/feedback", json={
        "session_id": "test-session",
        "agent_id": "agent-001",
        "action": "invalid_action",
    })
    assert resp.status_code == 422


async def test_feedback_with_state_manager():
    """POST /api/feedback 有 state_manager 时写入反馈到 Redis"""
    app = create_assist_app()
    app.state.classifier = None
    app.state.assist_orchestrator = None
    app.state.temporal_client = None

    # Mock state_manager
    mock_state_manager = AsyncMock()
    mock_state_manager.read_state = AsyncMock(return_value={
        "version": 3,
        "last_confidence": 0.8,
    })
    mock_state_manager.cas_write = AsyncMock(return_value={"ok": True, "new_version": 4})
    app.state.state_manager = mock_state_manager

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/feedback", json={
            "session_id": "test-session-sm",
            "agent_id": "agent-002",
            "action": "modify",
            "modify_fields": ["script_content", "knowledge_summary"],
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "modify"
    assert data["confidence"] == 0.5

    # 验证 state_manager 被正确调用
    mock_state_manager.read_state.assert_awaited_once_with("test-session-sm")
    mock_state_manager.cas_write.assert_awaited_once()
    call_kwargs = mock_state_manager.cas_write.call_args
    assert call_kwargs.kwargs["session_id"] == "test-session-sm"
    assert call_kwargs.kwargs["expected_version"] == 3
    assert call_kwargs.kwargs["writer"] == "feedback:agent-002"
    patches = call_kwargs.kwargs["patches"]
    assert "last_feedback" in patches
    assert patches["last_feedback"]["action"] == "modify"
    assert patches["last_feedback"]["confidence"] == 0.5
    assert patches["last_feedback"]["modify_fields"] == ["script_content", "knowledge_summary"]
    assert patches["last_feedback"]["agent_id"] == "agent-002"


async def test_feedback_no_state_no_cas_write():
    """POST /api/feedback state_manager 存在但 session 不存在时不调用 cas_write"""
    app = create_assist_app()
    app.state.classifier = None
    app.state.assist_orchestrator = None
    app.state.temporal_client = None

    mock_state_manager = AsyncMock()
    mock_state_manager.read_state = AsyncMock(return_value=None)
    mock_state_manager.cas_write = AsyncMock()
    app.state.state_manager = mock_state_manager

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/feedback", json={
            "session_id": "nonexistent-session",
            "agent_id": "agent-003",
            "action": "accept",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["confidence"] == 1.0
    # cas_write 不应该被调用
    mock_state_manager.cas_write.assert_not_awaited()
