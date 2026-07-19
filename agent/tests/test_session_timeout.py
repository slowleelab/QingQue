"""会话超时管理器测试（Redis ZSET 分布式方案）"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from smartcs.services.common.session_timeout import SessionTimeoutManager
from smartcs.shared.exceptions import InvalidTransitionError
from smartcs.shared.models import SessionPhase, SessionSubPhase


def _make_session_manager() -> AsyncMock:
    """构造模拟 SessionManager"""
    mgr = AsyncMock()
    mgr.transition_phase = AsyncMock()
    return mgr


def _make_mock_redis() -> AsyncMock:
    """构造模拟 Redis 客户端"""
    redis = AsyncMock()
    redis.zadd = AsyncMock(return_value=1)
    redis.zrem = AsyncMock(return_value=1)
    redis.zrangebyscore = AsyncMock(return_value=[])
    return redis


# ── _handle_timeout 直接测试 ──


@pytest.mark.asyncio
async def test_queue_timeout_falls_back_to_bot() -> None:
    """排队超时应回退到 BOT 阶段"""
    mock_mgr = _make_session_manager()
    mock_redis = _make_mock_redis()
    timeout_mgr = SessionTimeoutManager(mock_mgr, redis=mock_redis)

    await timeout_mgr._handle_timeout("sess-001", SessionSubPhase.AG_QUEUED)

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
    mock_redis = _make_mock_redis()
    timeout_mgr = SessionTimeoutManager(mock_mgr, redis=mock_redis)

    await timeout_mgr._handle_timeout("sess-002", SessionSubPhase.AG_REVIEWING)

    mock_mgr.transition_phase.assert_called_once_with(
        "sess-002",
        SessionPhase.ENDED,
        reason="review_timeout",
    )


@pytest.mark.asyncio
async def test_ringing_timeout_ends_session() -> None:
    """振铃超时应结束会话"""
    mock_mgr = _make_session_manager()
    mock_redis = _make_mock_redis()
    timeout_mgr = SessionTimeoutManager(mock_mgr, redis=mock_redis)

    await timeout_mgr._handle_timeout("sess-003", SessionSubPhase.AG_ASSIGNED)

    mock_mgr.transition_phase.assert_called_once_with(
        "sess-003",
        SessionPhase.ENDED,
        reason="agent:assigned_timeout",
    )


# ── start_guard / cancel_guard 测试 ──


@pytest.mark.asyncio
async def test_start_guard_writes_to_redis() -> None:
    """start_guard 应写入 Redis ZSET"""
    mock_mgr = _make_session_manager()
    mock_redis = _make_mock_redis()
    timeout_mgr = SessionTimeoutManager(mock_mgr, redis=mock_redis)
    timeout_mgr._bot_idle_timeout = 60

    timeout_mgr.start_guard("sess-004", SessionSubPhase.BOT_ACTIVE)

    # 等待 asyncio.create_task 完成
    await asyncio.sleep(0.05)

    mock_redis.zadd.assert_called_once()
    assert timeout_mgr.active_guards == 1


@pytest.mark.asyncio
async def test_cancel_guard_removes_from_redis() -> None:
    """cancel_guard 应从 Redis ZSET 移除"""
    mock_mgr = _make_session_manager()
    mock_redis = _make_mock_redis()
    timeout_mgr = SessionTimeoutManager(mock_mgr, redis=mock_redis)
    timeout_mgr._bot_idle_timeout = 60

    timeout_mgr.start_guard("sess-005", SessionSubPhase.BOT_ACTIVE)
    await asyncio.sleep(0.05)
    timeout_mgr.cancel_guard("sess-005")
    await asyncio.sleep(0.05)

    # zrem 至少被调用一次（start_guard 内部也会调 cancel_guard 清理旧守卫）
    assert mock_redis.zrem.call_count >= 1
    assert timeout_mgr.active_guards == 0


@pytest.mark.asyncio
async def test_start_guard_cancels_previous() -> None:
    """启动新守卫应取消旧守卫"""
    mock_mgr = _make_session_manager()
    mock_redis = _make_mock_redis()
    timeout_mgr = SessionTimeoutManager(mock_mgr, redis=mock_redis)

    timeout_mgr.start_guard("sess-006", SessionSubPhase.AG_QUEUED)
    await asyncio.sleep(0.05)
    timeout_mgr.start_guard("sess-006", SessionSubPhase.AG_ASSIGNED)
    await asyncio.sleep(0.05)

    # zrem 应被调用一次（取消旧守卫）
    assert mock_redis.zrem.call_count >= 1
    assert timeout_mgr.active_guards == 1


# ── 异常处理 ──


@pytest.mark.asyncio
async def test_invalid_transition_silently_handled() -> None:
    """超时触发的转换如果非法应静默处理"""
    mock_mgr = _make_session_manager()
    mock_mgr.transition_phase.side_effect = InvalidTransitionError("test")
    mock_redis = _make_mock_redis()
    timeout_mgr = SessionTimeoutManager(mock_mgr, redis=mock_redis)

    # 不应抛出异常
    await timeout_mgr._handle_timeout("sess-007", SessionSubPhase.AG_QUEUED)

    mock_mgr.transition_phase.assert_called_once()


# ── 轮询器测试 ──


@pytest.mark.asyncio
async def test_poller_picks_up_expired_sessions() -> None:
    """轮询器应拾取到期会话并触发超时"""
    mock_mgr = _make_session_manager()
    mock_redis = _make_mock_redis()

    # 模拟一个到期会话
    mock_redis.zrangebyscore.return_value = [b"sess-expired"]
    # 第一次 zrem 返回 1（成功），后续返回 0
    mock_redis.zrem = AsyncMock(side_effect=[1, 0])

    timeout_mgr = SessionTimeoutManager(mock_mgr, redis=mock_redis)
    timeout_mgr._bot_idle_timeout = 0

    # 手动放入内存缓存
    timeout_mgr._guards["sess-expired"] = (SessionSubPhase.BOT_ACTIVE, time.time() - 1)

    # 启动轮询器
    await timeout_mgr.start_poller()

    # 等待轮询周期
    await asyncio.sleep(6.5)

    await timeout_mgr.stop_poller()

    mock_mgr.transition_phase.assert_called_once()
