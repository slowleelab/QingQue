"""bot/router.py 核心逻辑单元测试 (Worker/队列/幂等/死信)"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lumio.services.bot import router as bot_router
from lumio.shared.auth import AuthorizationError, AuthUser

# ── _quick_intent_match ──


def test_quick_intent_match_domain_mapping():
    """rule 匹配 → domain 别名映射"""
    loader = MagicMock()
    loader.match = MagicMock(return_value=("card", 0.9))
    with patch.object(bot_router, "_rule_loader", loader):
        assert bot_router._quick_intent_match("挂失") == "lost_card"


def test_quick_intent_match_default():
    """未映射 → default"""
    loader = MagicMock()
    loader.match = MagicMock(return_value=("unknown_domain", 0.0))
    with patch.object(bot_router, "_rule_loader", loader):
        assert bot_router._quick_intent_match("随便聊聊") == "default"


# ── _ensure_session_owned ──


def test_ensure_session_owned_match():
    """JWT session 与请求一致 → 放行"""
    user = AuthUser(user_id="u1", role="customer", session_id="s1")
    bot_router._ensure_session_owned(user, "s1")


def test_ensure_session_owned_no_session():
    """JWT 无 session_id → 放行"""
    user = AuthUser(user_id="u1", role="customer")
    bot_router._ensure_session_owned(user, "any")


def test_ensure_session_owned_mismatch():
    """JWT session 与请求不一致 → 403"""
    user = AuthUser(user_id="u1", role="customer", session_id="s1")
    with pytest.raises(AuthorizationError):
        bot_router._ensure_session_owned(user, "s2")


# ── _build_poll_json ──


def test_build_poll_json_done():
    """done + 有回复 → has_message True"""
    data = bot_router._build_poll_json(status="done", reply="你好", intent="faq", confidence=0.8, source="template")
    assert data["has_message"] is True
    assert data["reply"] == "你好"
    assert data["confidence"] == 0.8


def test_build_poll_json_no_message():
    """无回复 → has_message False"""
    data = bot_router._build_poll_json(status="processing", position=2, est_wait="3s", suggestion="请稍候")
    assert data["has_message"] is False
    assert data["position"] == 2
    assert "suggestion" in data


def test_build_poll_json_empty_reply():
    """空回复 → has_message False"""
    data = bot_router._build_poll_json(status="done", reply="")
    assert data["has_message"] is False


# ── _finish_message ──


async def test_finish_message_writes_and_publishes():
    """写 response key + 发布通知"""
    redis = AsyncMock()
    with patch("lumio.shared.safety.safety_filter.filter_output", return_value="安全回复"):
        await bot_router._finish_message(redis, "s1", "回复内容", intent="faq", confidence=0.9, source="llm")
    redis.setex.assert_awaited_once()
    redis.publish.assert_awaited_once_with(bot_router.NOTIFY_CHANNEL_PREFIX + ":s1", "ready")
    payload = json.loads(redis.setex.call_args.args[2])
    assert payload["status"] == "done"
    assert payload["reply"] == "安全回复"  # 已过滤


# ── _mark_processed ──


async def test_mark_processed_empty_id():
    """空 id 跳过"""
    redis = AsyncMock()
    await bot_router._mark_processed(redis, "")
    redis.setex.assert_not_awaited()


async def test_mark_processed_sets_key():
    """标记幂等键"""
    redis = AsyncMock()
    await bot_router._mark_processed(redis, "msg-1")
    assert redis.setex.await_args.args[0] == f"{bot_router._PROCESSED_PREFIX}:msg-1"
    assert redis.setex.await_args.args[1] == 300


# ── _init_stream_group ──


async def test_init_stream_group_success():
    """创建 consumer group"""
    redis = AsyncMock()
    await bot_router._init_stream_group(redis)
    redis.xgroup_create.assert_awaited_once()


async def test_init_stream_group_already_exists():
    """group 已存在 → 忽略异常"""
    redis = AsyncMock()
    redis.xgroup_create.side_effect = Exception("BUSYGROUP")
    await bot_router._init_stream_group(redis)  # 不抛


# ── _dispatch_message ──


async def test_dispatch_message_no_session_id():
    """缺 session_id → XACK 丢弃"""
    redis = AsyncMock()
    await bot_router._dispatch_message(redis, None, "m1", {})
    redis.xack.assert_awaited_once()


async def test_dispatch_message_new_session_spawns_worker():
    """新 session → 启动 Worker + 入队"""
    redis = AsyncMock()
    bot_router._session_queues.clear()
    bot_router._session_active.clear()
    with patch.object(bot_router, "_session_worker") as mock_worker:
        await bot_router._dispatch_message(redis, None, "m1", {"session_id": "s1", "message": "hi"})
    assert "s1" in bot_router._session_queues
    assert "s1" in bot_router._session_active
    assert bot_router._session_queues["s1"].qsize() == 1
    mock_worker.assert_called_once()
    # 清理
    bot_router._session_queues.clear()
    bot_router._session_active.clear()


async def test_dispatch_message_existing_session():
    """已有 session → 仅入队"""
    bot_router._session_queues.clear()
    bot_router._session_active.clear()
    q = asyncio.Queue()
    bot_router._session_queues["s1"] = q
    bot_router._session_active["s1"] = True
    with patch.object(bot_router, "_session_worker") as mock_worker:
        await bot_router._dispatch_message(AsyncMock(), None, "m2", {"session_id": "s1"})
    assert q.qsize() == 1
    mock_worker.assert_not_called()  # 不重复启动
    bot_router._session_queues.clear()
    bot_router._session_active.clear()


# ── _claim_stale ──


async def test_claim_stale_normal_redispatch(monkeypatch):
    """认领消息重试次数未超限 → 重新分发"""
    bot_router._dispatch_message = AsyncMock()  # fire-and-forget 立即完成, 防残留 task
    redis = AsyncMock()
    redis.xautoclaim = AsyncMock(return_value=(None, [("m1", {"session_id": "s1", "message": "hi"})]))
    redis.hincrby = AsyncMock(return_value=1)  # 第 1 次重试
    agent = MagicMock()

    _real_sleep = asyncio.sleep  # 保存原始 sleep, 防递归

    async def fast_sleep(seconds):
        await _real_sleep(0.001)  # 缩短循环间隔

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    bot_router._session_queues.clear()
    bot_router._session_active.clear()
    # wait_for 超时终止循环 (无异常控制流, 兼容 pytest-asyncio)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(bot_router._claim_stale(redis, agent), timeout=0.2)
    await asyncio.sleep(0.05)  # 让 fire-and-forget dispatch task 执行
    # 消息被重新分发 (AsyncMock 被调用)
    bot_router._dispatch_message.assert_awaited()
    bot_router._session_queues.clear()
    bot_router._session_active.clear()


async def test_claim_stale_retry_exhausted_to_dead_letter(monkeypatch):
    """重试超限 → 死信队列 + ACK + 指标"""
    bot_router._dispatch_message = AsyncMock()  # 防残留 task
    redis = AsyncMock()
    redis.xautoclaim = AsyncMock(return_value=(None, [("m1", {"session_id": "s1", "message": "hi"})]))
    redis.hincrby = AsyncMock(return_value=4)  # 超过 MAX_RETRY_COUNT
    agent = MagicMock()

    _real_sleep = asyncio.sleep  # 保存原始 sleep, 防递归

    async def fast_sleep(seconds):
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(bot_router._claim_stale(redis, agent), timeout=0.2)
    # 写入死信 + ACK + 清理计数 (循环多次处理同一消息, 断言至少一次)
    redis.xadd.assert_awaited()
    redis.xack.assert_awaited()
    redis.hdel.assert_awaited()


async def test_claim_stale_error_loop_continues(monkeypatch):
    """异常被捕获, 循环继续"""
    bot_router._dispatch_message = AsyncMock()  # 防残留 task
    redis = AsyncMock()
    redis.xautoclaim = AsyncMock(side_effect=RuntimeError("redis down"))

    _real_sleep = asyncio.sleep  # 保存原始 sleep, 防递归

    async def fast_sleep(seconds):
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(bot_router._claim_stale(redis, MagicMock()), timeout=0.2)


# ── 快速兜底话术 ──


def test_fast_replies_has_expected_keys():
    """紧急话术覆盖关键场景"""
    assert "lost_card" in bot_router._FAST_REPLIES  # 挂失 (key 为 lost_card)
    assert "complaint" in bot_router._FAST_REPLIES
    assert "bill_query" in bot_router._FAST_REPLIES
    assert "default" in bot_router._FAST_REPLIES
