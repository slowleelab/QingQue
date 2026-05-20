"""会话超时管理器测试"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.common.session_timeout import SessionTimeoutManager
from smartcs.shared.models import SessionPhase, SessionSubPhase


def _make_session_manager() -> AsyncMock:
    """构造模拟 SessionManager"""
    mgr = AsyncMock()
    mgr.transition_phase = AsyncMock()
    return mgr


@pytest.mark.asyncio
async def test_queue_timeout_falls_back_to_bot() -> None:
    """排队超时应回退到 BOT 阶段"""
    mock_mgr = _make_session_manager()
    timeout_mgr = SessionTimeoutManager(mock_mgr)

    # 使用极短超时测试
    timeout_mgr._queue_timeout = 0  # 立即超时

    timeout_mgr.start_guard("sess-001", SessionSubPhase.AG_QUEUED)

    # 等待超时触发
    await asyncio.sleep(0.1)

    mock_mgr.transition_phase.assert_called_once_with(
        "sess-001",
        SessionPhase.BOT,
        new_sub_phase=SessionSubPhase.BOT_ACTIVE,
        reason="queue_timeout",
    )


@pytest.mark.asyncio
async def test_review_timeout_ends_session() -> None:
    """话后小结超时应结束会话"""
    mock_mgr = _make_session_manager()
    timeout_mgr = SessionTimeoutManager(mock_mgr)
    timeout_mgr._review_timeout = 0

    timeout_mgr.start_guard("sess-002", SessionSubPhase.AG_REVIEWING)

    await asyncio.sleep(0.1)

    mock_mgr.transition_phase.assert_called_once_with(
        "sess-002",
        SessionPhase.ENDED,
        reason="review_timeout",
    )


@pytest.mark.asyncio
async def test_ringing_timeout_ends_session() -> None:
    """振铃超时应结束会话"""
    mock_mgr = _make_session_manager()
    timeout_mgr = SessionTimeoutManager(mock_mgr)
    timeout_mgr._ringing_timeout = 0

    timeout_mgr.start_guard("sess-003", SessionSubPhase.AG_ASSIGNED)

    await asyncio.sleep(0.1)

    mock_mgr.transition_phase.assert_called_once_with(
        "sess-003",
        SessionPhase.ENDED,
        reason="agent:assigned_timeout",
    )


@pytest.mark.asyncio
async def test_cancel_guard_prevents_timeout() -> None:
    """取消守卫应阻止超时触发"""
    mock_mgr = _make_session_manager()
    timeout_mgr = SessionTimeoutManager(mock_mgr)
    timeout_mgr._queue_timeout = 0

    timeout_mgr.start_guard("sess-004", SessionSubPhase.AG_QUEUED)
    timeout_mgr.cancel_guard("sess-004")

    await asyncio.sleep(0.1)

    mock_mgr.transition_phase.assert_not_called()


@pytest.mark.asyncio
async def test_start_guard_cancels_previous() -> None:
    """启动新守卫应取消旧守卫"""
    mock_mgr = _make_session_manager()
    timeout_mgr = SessionTimeoutManager(mock_mgr)
    timeout_mgr._queue_timeout = 0
    timeout_mgr._ringing_timeout = 10  # 不会触发

    # 先排队，后振铃（排队守卫应被取消）
    timeout_mgr.start_guard("sess-005", SessionSubPhase.AG_QUEUED)
    timeout_mgr.start_guard("sess-005", SessionSubPhase.AG_ASSIGNED)

    await asyncio.sleep(0.1)

    # 不应触发排队超时
    mock_mgr.transition_phase.assert_not_called()
    assert timeout_mgr.active_guards == 1


@pytest.mark.asyncio
async def test_invalid_transition_silently_handled() -> None:
    """超时触发的转换如果非法应静默处理"""
    mock_mgr = _make_session_manager()
    mock_mgr.transition_phase.side_effect = ValueError("非法状态转换")
    timeout_mgr = SessionTimeoutManager(mock_mgr)
    timeout_mgr._queue_timeout = 0

    timeout_mgr.start_guard("sess-006", SessionSubPhase.AG_QUEUED)

    # 不应抛出异常
    await asyncio.sleep(0.1)

    mock_mgr.transition_phase.assert_called_once()
