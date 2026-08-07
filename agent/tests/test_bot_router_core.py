"""bot/router.py 核心逻辑单元测试 (Worker/队列/幂等/死信)"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lumio.services.bot import router as bot_router
from lumio.shared.auth import AuthorizationError, AuthUser
from lumio.shared.models import SessionPhase, SessionSubPhase

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


# ── _run_agent ──


def _make_agent(result: dict | None = None, *, session_state=None, timeout=False):
    """构造 mock agent"""
    from datetime import datetime

    from lumio.shared.models import SessionState

    agent = MagicMock()
    agent._session_manager = MagicMock()
    sm = agent._session_manager

    state = session_state or SessionState(
        session_id="s1",
        customer_id="c1",
        current_phase=SessionPhase.BOT,
        sub_phase=SessionSubPhase.BOT_ACTIVE,
        created_at=datetime.now(),
        last_active_at=datetime.now(),
    )
    sm.get_or_create = AsyncMock(return_value=state)
    sm.get_session = AsyncMock(return_value=state)
    sm.transition_phase = AsyncMock(return_value=state)
    sm.add_turn = AsyncMock()
    sm.get_history = AsyncMock(return_value=[])
    sm._save_meta = AsyncMock()

    if timeout:

        async def slow_run(*a, **kw):
            import asyncio

            await asyncio.sleep(5)

        agent.run = slow_run
    else:
        agent.run = AsyncMock(
            return_value=result
            or {
                "response": "这是回复",
                "response_source": "llm",
                "intent": None,
                "should_transfer": False,
                "transfer_reason": "",
                "entities": [],
                "retrieval_context": "",
            }
        )
    agent._chat_client = None
    return agent


def _make_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.xack = AsyncMock()
    redis.setex = AsyncMock()
    redis.publish = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    return redis


async def test_run_agent_normal_path():
    """正常路径: 保存历史 + 回复 + XACK + 幂等标记"""

    agent = _make_agent()
    redis = _make_redis()
    await bot_router._run_agent(redis, agent, "s1", "年费多少", "m1", "orig-1")
    # 历史保存 (客户 + bot 两条)
    assert agent._session_manager.add_turn.await_count == 2
    # 回复写入 + 通知
    redis.setex.assert_awaited()
    redis.publish.assert_awaited()
    redis.xack.assert_awaited_once_with(bot_router.CHAT_STREAM_KEY, bot_router.CONSUMER_GROUP, "m1")


async def test_run_agent_ended_revival():
    """ENDED 会话复活为 BOT_ACTIVE"""
    from datetime import datetime

    from lumio.shared.models import SessionState

    state = SessionState(
        session_id="s1",
        customer_id="c1",
        current_phase=SessionPhase.ENDED,
        sub_phase=None,
        created_at=datetime.now(),
        last_active_at=datetime.now(),
        end_reason="customer_ended",
    )
    agent = _make_agent(session_state=state)
    redis = _make_redis()
    await bot_router._run_agent(redis, agent, "s1", "我又回来了", "m1", "orig-1")
    # 复活转换被调用
    kwargs = agent._session_manager.transition_phase.call_args.kwargs
    assert kwargs["reason"] == "customer_returned"


async def test_run_agent_agent_phase_skips():
    """AGENT 阶段: 消息写入历史 + 排队话术 + XACK"""
    from datetime import datetime

    from lumio.shared.models import SessionState

    state = SessionState(
        session_id="s1",
        customer_id="c1",
        current_phase=SessionPhase.AGENT,
        sub_phase=SessionSubPhase.AG_QUEUED,
        created_at=datetime.now(),
        last_active_at=datetime.now(),
    )
    agent = _make_agent(session_state=state)
    redis = _make_redis()
    await bot_router._run_agent(redis, agent, "s1", "排队中的消息", "m1", "orig-1")
    # 客户消息写入历史
    agent._session_manager.add_turn.assert_awaited_once()
    # 排队话术
    payload = redis.setex.call_args.args[2]
    assert "已为您记录" in payload
    redis.xack.assert_awaited_once()


async def test_run_agent_timeout():
    """编排超时 → 超时话术 + XACK + 幂等"""
    from lumio.shared.config import get_settings

    settings = get_settings()
    original_timeout = settings.orchestration.global_timeout_ms
    settings.orchestration.global_timeout_ms = 50  # 缩短编排超时
    try:
        agent = _make_agent(timeout=True)  # agent.run 挂起 5s
        redis = _make_redis()
        await bot_router._run_agent(redis, agent, "s1", "消息", "m1", "orig-1")
    finally:
        settings.orchestration.global_timeout_ms = original_timeout
    # 找含超时话术的 setex 调用 (最后一次 setex 可能是幂等标记 "1")
    payloads = [c.args[2] for c in redis.setex.call_args_list if len(c.args) >= 3]
    assert any("回复超时" in p for p in payloads)
    redis.xack.assert_awaited_once()


async def test_run_agent_transfer():
    """转人工: 无 chat_client 时降级继续"""
    agent = _make_agent(
        result={
            "response": "为您转接人工",
            "response_source": "transfer",
            "intent": None,
            "should_transfer": True,
            "transfer_reason": "customer_request",
            "entities": [],
            "retrieval_context": "",
        }
    )
    agent._chat_client = None  # 无 chat-svc → 降级
    redis = _make_redis()
    await bot_router._run_agent(redis, agent, "s1", "转人工", "m1", "orig-1")
    # 转人工仍正常回复
    redis.setex.assert_awaited()


# ── _session_worker ─────────────────────────────────────────────


class _FakeSem:
    """locked() 可控 + async with 直接通过 (避免真实 Semaphore 挂起)"""

    def __init__(self, locked: bool = False) -> None:
        self._locked = locked

    def locked(self) -> bool:
        return self._locked

    async def __aenter__(self) -> "_FakeSem":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


async def _run_worker(monkeypatch, redis, q, agent, *, sem_locked: bool = False, db_factory=None) -> None:
    """以 1ms 空闲超时运行 worker (空队列快速退出, 无需等 300s)"""
    _real_wait_for = asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):  # noqa: ARG001
        return await _real_wait_for(awaitable, 0.001)

    monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)
    bot_router._agent_semaphore = _FakeSem(locked=sem_locked)
    bot_router._db_session_factory = db_factory
    bot_router._metrics = {"to": 0, "fr": 0, "mg": 0}
    await bot_router._session_worker("s1", q, redis, agent)


def _make_msg(msg_id: str, text: str, *, enqueue: float | None = None, extra: dict | None = None) -> tuple[str, dict]:
    """enqueue 缺省用当前 loop 时间 (新鲜消息); 过期消息传 loop.time() - 100"""
    fields = {"message": text, "message_id": msg_id}
    if enqueue is None:
        enqueue = asyncio.get_event_loop().time()
    fields["_enqueue_time"] = enqueue
    if extra:
        fields.update(extra)
    return (msg_id, fields)


async def test_session_worker_normal_path(monkeypatch):
    """标准路径: 审计落库 + Agent 处理 + XACK"""
    redis = _make_redis()
    q = asyncio.Queue()
    agent = _make_agent()
    with patch.object(bot_router, "_run_agent", new=AsyncMock()) as run_agent, patch.object(
        bot_router, "write_chat_message", new=AsyncMock()
    ) as write_msg:
        q.put_nowait(_make_msg("m1", "hi", extra={"customer_id": "c1", "channel": "web"}))
        await _run_worker(monkeypatch, redis, q, agent, db_factory=object())
    write_msg.assert_awaited_once()
    run_agent.assert_awaited_once()
    args = run_agent.await_args.args
    assert args[2] == "s1"  # session_id
    assert args[3] == "hi"  # merged_message
    assert args[4] == "m1"  # msg_id
    assert args[5] == "m1"  # orig_message_id
    redis.xack.assert_awaited_with("lumio:chat:stream", "bot-group", "m1")


async def test_session_worker_skips_expired_message(monkeypatch):
    """消息超过 TTL → 快速兜底话术 + SKIPPED 落库 + 幂等标记"""
    redis = _make_redis()
    q = asyncio.Queue()
    agent = _make_agent()
    with patch.object(bot_router, "_run_agent", new=AsyncMock()) as run_agent, patch.object(
        bot_router, "update_chat_message", new=AsyncMock()
    ) as update_msg:
        q.put_nowait(_make_msg("m1", "hi", enqueue=asyncio.get_event_loop().time() - 100))
        await _run_worker(monkeypatch, redis, q, agent, db_factory=object())
    run_agent.assert_not_awaited()
    update_msg.assert_awaited_once()
    assert update_msg.await_args.kwargs["processing_status"] == bot_router.ChatMessageStatus.SKIPPED
    assert update_msg.await_args.kwargs["source"] == "timeout"
    # 快速兜底话术写入 response key
    redis.setex.assert_awaited()
    redis.publish.assert_awaited()
    redis.xack.assert_awaited_with("lumio:chat:stream", "bot-group", "m1")
    # 幂等标记
    assert bot_router._metrics["to"] == 1


async def test_session_worker_skips_duplicate(monkeypatch):
    """消息已处理 (幂等键命中) → 直接 XACK, 不重复执行"""
    redis = _make_redis()
    redis.get = AsyncMock(return_value="1")  # 命中 processed 幂等键
    q = asyncio.Queue()
    agent = _make_agent()
    with patch.object(bot_router, "_run_agent", new=AsyncMock()) as run_agent:
        q.put_nowait(_make_msg("m1", "hi", ))
        await _run_worker(monkeypatch, redis, q, agent)
    run_agent.assert_not_awaited()
    redis.xack.assert_awaited_with("lumio:chat:stream", "bot-group", "m1")


async def test_session_worker_fast_reply(monkeypatch):
    """Semaphore 满荷 + 冷却期外 → 快速兜底话术 (不调 Agent)"""
    redis = _make_redis()
    q = asyncio.Queue()
    agent = _make_agent()
    bot_router._rule_loader.load_from_memory()  # 种子规则: 账单→bill_query
    with patch.object(bot_router, "_run_agent", new=AsyncMock()) as run_agent, patch.object(
        bot_router, "update_chat_message", new=AsyncMock()
    ) as update_msg, patch.object(bot_router, "write_chat_message", new=AsyncMock()):
        q.put_nowait(_make_msg("m1", "账单", ))
        await _run_worker(monkeypatch, redis, q, agent, sem_locked=True, db_factory=object())
    run_agent.assert_not_awaited()
    update_msg.assert_awaited_once()
    assert update_msg.await_args.kwargs["source"] == "fast_reply"
    assert update_msg.await_args.kwargs["intent"] == "bill_query"
    # 冷却时间戳写入
    redis.set.assert_awaited()
    assert bot_router._metrics["fr"] == 1
    redis.xack.assert_awaited()


async def test_session_worker_cooldown_active(monkeypatch):
    """满荷但在冷却期内 → 不做二次快速兜底, 走标准 Agent 路径"""
    import time as _time

    redis = _make_redis()

    def get_side(key: str):
        if key.startswith("lumio:processed"):
            return None
        if key.startswith("lumio:fast_reply"):
            return str(_time.time())  # 冷却期内的时间戳
        return None

    redis.get = AsyncMock(side_effect=get_side)
    q = asyncio.Queue()
    agent = _make_agent()
    with patch.object(bot_router, "_run_agent", new=AsyncMock()) as run_agent:
        q.put_nowait(_make_msg("m1", "账单", ))
        await _run_worker(monkeypatch, redis, q, agent, sem_locked=True)
    run_agent.assert_awaited_once()  # 冷却期内仍处理, 只是不兜底


async def test_session_worker_merges_queued_messages(monkeypatch):
    """队列中排队消息 → 合并一次 LLM 调用 + FIX-5 语义标记"""
    redis = _make_redis()
    q = asyncio.Queue()
    agent = _make_agent()
    with patch.object(bot_router, "_run_agent", new=AsyncMock()) as run_agent:
        q.put_nowait(_make_msg("m1", "第一句", ))
        q.put_nowait(_make_msg("m2", "第二句", ))
        q.put_nowait(_make_msg("m3", "第三句", ))
        await _run_worker(monkeypatch, redis, q, agent)
    run_agent.assert_awaited_once()
    merged = run_agent.await_args.args[3]
    assert "（用户连续发送了 3 条消息" in merged
    assert "第一句" in merged and "第三句" in merged
    assert run_agent.await_args.kwargs["merged_message_ids"] == ["m2", "m3"]
    assert bot_router._metrics["mg"] == 2
    # 合并的消息也 XACK
    assert redis.xack.await_count == 3


async def test_session_worker_merge_overflow(monkeypatch):
    """合并上限 5 条 → 超限消息放回队列, 下一轮单独处理"""
    redis = _make_redis()
    q = asyncio.Queue()
    agent = _make_agent()
    with patch.object(bot_router, "_run_agent", new=AsyncMock()) as run_agent:
        for i in range(1, 8):
            q.put_nowait(_make_msg(f"m{i}", f"消息{i}"))
        await _run_worker(monkeypatch, redis, q, agent)
    assert run_agent.await_count == 2
    first = run_agent.await_args_list[0]
    assert "（用户连续发送了 6 条消息" in first.args[3]  # 主消息 + 合并 5 条
    assert len(first.kwargs["merged_message_ids"]) == 5
    second = run_agent.await_args_list[1]
    assert second.args[3] == "消息7"  # 超限消息未被丢弃, 下一轮处理
    assert second.kwargs["merged_message_ids"] == []


async def test_session_worker_merge_skips_expired_pending(monkeypatch):
    """排队消息已过期 → 不合并, SKIPPED 落库"""
    redis = _make_redis()
    q = asyncio.Queue()
    agent = _make_agent()
    with patch.object(bot_router, "_run_agent", new=AsyncMock()) as run_agent, patch.object(
        bot_router, "update_chat_message", new=AsyncMock()
    ) as update_msg:
        q.put_nowait(_make_msg("m1", "新的", ))
        q.put_nowait(_make_msg("m2", "过期消息", enqueue=asyncio.get_event_loop().time() - 100))
        await _run_worker(monkeypatch, redis, q, agent, db_factory=object())
    run_agent.assert_awaited_once()
    assert run_agent.await_args.args[3] == "新的"  # 未合并过期消息
    update_msg.assert_awaited_once()
    assert update_msg.await_args.kwargs["processing_status"] == bot_router.ChatMessageStatus.SKIPPED


async def test_session_worker_agent_error_dead_letter(monkeypatch):
    """Agent 异常 → 兜底话术 + ERROR 落库 + 死信队列 + XACK"""
    redis = _make_redis()
    q = asyncio.Queue()
    agent = _make_agent()
    with patch.object(bot_router, "_run_agent", new=AsyncMock(side_effect=RuntimeError("boom"))), patch.object(
        bot_router, "update_chat_message", new=AsyncMock()
    ) as update_msg:
        q.put_nowait(_make_msg("m1", "hi", ))
        await _run_worker(monkeypatch, redis, q, agent, db_factory=object())
    update_msg.assert_awaited_once()
    assert update_msg.await_args.kwargs["processing_status"] == bot_router.ChatMessageStatus.ERROR
    assert update_msg.await_args.kwargs["source"] == "error_fallback"
    # 死信队列写入
    redis.xadd.assert_awaited()
    assert redis.xadd.await_args.args[0] == bot_router.DEAD_LETTER_KEY
    redis.xack.assert_awaited()


async def test_session_worker_otel_context_detach(monkeypatch):
    """Stream 消息带 trace context → 链接 Span 并在 finally 释放"""
    redis = _make_redis()
    q = asyncio.Queue()
    agent = _make_agent()
    with patch.object(bot_router, "_run_agent", new=AsyncMock()) as run_agent:
        q.put_nowait(
            _make_msg(
                "m1",
                "hi",
                extra={"_trace_context": "0000000000000001:0000000000000002:01"},
            )
        )
        await _run_worker(monkeypatch, redis, q, agent)
    run_agent.assert_awaited_once()  # 无异常且正常处理


async def test_session_worker_otel_invalid_trace(monkeypatch):
    """非法 trace context → 静默忽略, 不影响处理"""
    redis = _make_redis()
    q = asyncio.Queue()
    agent = _make_agent()
    with patch.object(bot_router, "_run_agent", new=AsyncMock()) as run_agent:
        q.put_nowait(_make_msg("m1", "hi", extra={"_trace_context": "not-hex!"}))
        await _run_worker(monkeypatch, redis, q, agent)
    run_agent.assert_awaited_once()


async def test_session_worker_cancelled(monkeypatch):
    """Worker 收到取消信号 → 清理活跃注册"""
    redis = _make_redis()
    q = asyncio.Queue()
    agent = _make_agent()
    bot_router._session_queues["s1"] = q
    bot_router._session_active["s1"] = True
    task = asyncio.create_task(
        bot_router._session_worker("s1", q, redis, agent)
    )
    await asyncio.sleep(0)  # 让 worker 启动并挂起在 wait_for
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "s1" not in bot_router._session_queues
    assert "s1" not in bot_router._session_active


# ── _consumer_loop ──────────────────────────────────────────────


async def test_consumer_loop_dispatches_batch():
    """XREADGROUP 批量 → create_task 并行分发"""
    redis = AsyncMock()
    redis.xreadgroup = AsyncMock(
        side_effect=[
            [[bot_router.CHAT_STREAM_KEY, [["m1", {"session_id": "s1", "message": "hi"}]]]],
            [],
        ]
    )
    with patch.object(bot_router, "_dispatch_message", new=AsyncMock()) as dispatch:
        task = asyncio.create_task(bot_router._consumer_loop(redis, None))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert dispatch.await_count == 1
    fields = dispatch.await_args.args[3]
    assert "_enqueue_time" in fields  # 注入入队时间


async def test_consumer_loop_retries_on_error(monkeypatch):
    """XREADGROUP 异常 → 1s 后重试 (快速 sleep)"""
    redis = AsyncMock()
    redis.xreadgroup = AsyncMock(side_effect=[ConnectionError("down"), []])
    _real_sleep = asyncio.sleep

    async def fast_sleep(seconds):  # noqa: ARG001
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    with patch.object(bot_router, "_dispatch_message", new=AsyncMock()):
        task = asyncio.create_task(bot_router._consumer_loop(redis, None))
        await _real_sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert redis.xreadgroup.await_count >= 2  # 异常后重试


# ── _monitoring_loop ────────────────────────────────────────────


async def test_monitoring_loop_collects_metrics(monkeypatch):
    """监控循环: PEL/Stream/活跃 worker/semaphore 利用率"""
    redis = AsyncMock()
    redis.xpending = AsyncMock(return_value={"pending": 3})
    redis.xlen = AsyncMock(return_value=10)
    _real_sleep = asyncio.sleep

    async def fast_sleep(seconds):  # noqa: ARG001
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    bot_router._session_active["s1"] = True
    bot_router._agent_semaphore = _FakeSem()
    bot_router._agent_semaphore._value = 2  # type: ignore[attr-defined]
    task = asyncio.create_task(bot_router._monitoring_loop(redis))
    await _real_sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bot_router._metrics["p"] == 3
    assert bot_router._metrics["sl"] == 10
    assert bot_router._metrics["as"] == 1
    assert bot_router._metrics["su"] == 0.8  # 1 - 2/10
    bot_router._session_active.clear()


async def test_monitoring_loop_no_semaphore(monkeypatch):
    """无 Semaphore → 利用率记 0"""
    redis = AsyncMock()
    redis.xpending = AsyncMock(return_value={"pending": 0})
    redis.xlen = AsyncMock(return_value=0)
    _real_sleep = asyncio.sleep

    async def fast_sleep(seconds):  # noqa: ARG001
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    bot_router._agent_semaphore = None
    task = asyncio.create_task(bot_router._monitoring_loop(redis))
    await _real_sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bot_router._metrics["su"] == 0.0


# ── start/stop_bot_worker ───────────────────────────────────────


async def test_start_bot_worker_without_db():
    """无 DB → 内存种子规则兜底 + 启动全部后台协程"""
    from types import SimpleNamespace

    app = SimpleNamespace(
        state=SimpleNamespace(
            redis_client=AsyncMock(),
            agent=MagicMock(),
            db_session_factory=None,
        )
    )
    with patch.object(bot_router, "_consumer_loop", new=AsyncMock()), patch.object(
        bot_router, "_claim_stale", new=AsyncMock()
    ), patch.object(bot_router, "_monitoring_loop", new=AsyncMock()):
        await bot_router.start_bot_worker(app)
    assert bot_router._agent_semaphore is not None
    assert bot_router._rule_loader.rules  # 内存种子规则兜底
    assert app.state._consumer_task is not None
    assert app.state._claim_task is not None
    assert app.state._monitor_task is not None
    await bot_router.stop_bot_worker(app)
    assert app.state._consumer_task.done()


async def test_start_bot_worker_with_db():
    """有 DB → load_from_db + 热加载监听"""
    from types import SimpleNamespace

    app = SimpleNamespace(
        state=SimpleNamespace(
            redis_client=AsyncMock(),
            agent=MagicMock(),
            db_session_factory=MagicMock(),
        )
    )
    with patch.object(bot_router, "_consumer_loop", new=AsyncMock()), patch.object(
        bot_router, "_claim_stale", new=AsyncMock()
    ), patch.object(bot_router, "_monitoring_loop", new=AsyncMock()), patch.object(
        bot_router._rule_loader, "load_from_db", new=AsyncMock()
    ) as load_db, patch.object(
        bot_router._rule_loader, "start_hot_reload", new=AsyncMock()
    ) as hot_reload:
        await bot_router.start_bot_worker(app)
    load_db.assert_awaited_once()
    hot_reload.assert_awaited_once()
    await bot_router.stop_bot_worker(app)


# ── _run_agent 分支补充 (转人工桥接 / 合并审计 / 复活失败) ─────────


def _transfer_result():
    """should_transfer=True 且带意图的结果"""
    from lumio.shared.models import IntentLabel, IntentResult

    return {
        "response": "正在为您转接人工",
        "response_source": "transfer",
        "intent": IntentResult(primary_intent=IntentLabel.TRANSFER_AGENT, primary_confidence=0.95),
        "should_transfer": True,
        "transfer_reason": "客户要求",
        "entities": [{"entity_type": "card", "value": "1234"}],
        "retrieval_context": "",
    }


async def test_run_agent_transfer_with_chat_client():
    """转人工 + chat-svc 在线 → 创建会话 + 扩展 response key + 合并审计"""
    from datetime import datetime

    from lumio.shared.models import Entity, IntentLabel, IntentResult, SessionState

    agent = _make_agent(_transfer_result())
    sm = agent._session_manager
    state = SessionState(
        session_id="s1",
        customer_id="c1",
        current_phase=SessionPhase.BOT,
        sub_phase=SessionSubPhase.BOT_ACTIVE,
        created_at=datetime.now(),
        last_active_at=datetime.now(),
        conversation_summary="客户咨询账单",
        last_entities=[Entity(entity_type="card", value="1234")],
    )
    sm.get_session = AsyncMock(return_value=state)
    sm.get_history = AsyncMock(
        return_value=[MagicMock(speaker="user", content="问1"), MagicMock(speaker="bot", content="答1")]
    )
    chat_client = MagicMock()
    chat_client.build_transfer_request = MagicMock(return_value={"session_id": "s1", "reason": "客户要求"})
    chat_client.create_session = AsyncMock(return_value={"pollUrl": "http://poll/1", "sessionId": "sess-1"})
    agent._chat_client = chat_client

    redis = _make_redis()
    redis.get = AsyncMock(return_value=json.dumps({"status": "done", "reply": "x"}))

    with patch.object(bot_router, "update_chat_message", new=AsyncMock()) as update_msg:
        await bot_router._run_agent(
            redis, agent, "s1", "转人工", "m1", "orig-1", merged_message_ids=["mid-2"]
        )
    chat_client.create_session.assert_awaited_once()
    # response key 扩展: is_transfer + transfer_url
    setex_calls = [c for c in redis.setex.call_args_list if c.args[0].startswith(bot_router.RESPONSE_KEY_PREFIX)]
    assert setex_calls
    payload = json.loads(setex_calls[-1].args[2])
    assert payload["is_transfer"] is True
    assert payload["transfer_url"] == "http://poll/1"
    assert payload["transfer_reason"] == "客户要求"
    # 合并消息审计
    sources = [c.kwargs.get("source") for c in update_msg.call_args_list]
    assert "merged" in sources
    redis.xack.assert_awaited()


async def test_run_agent_transfer_chat_client_error():
    """转人工 + chat-svc 异常 → 降级提示 + 正常回复"""
    agent = _make_agent(_transfer_result())
    chat_client = MagicMock()
    chat_client.build_transfer_request = MagicMock(return_value={})
    chat_client.create_session = AsyncMock(side_effect=ConnectionError("chat-svc down"))
    agent._chat_client = chat_client
    redis = _make_redis()
    await bot_router._run_agent(redis, agent, "s1", "转人工", "m1", "orig-1")
    redis.setex.assert_awaited()  # 降级后仍回复
    redis.xack.assert_awaited()


async def test_run_agent_transfer_get_session_error():
    """转人工 + 重新加载会话失败 → 异常被吞, 降级提示"""
    agent = _make_agent(_transfer_result())
    sm = agent._session_manager
    sm.get_session = AsyncMock(side_effect=RuntimeError("boom"))
    chat_client = MagicMock()
    chat_client.build_transfer_request = MagicMock(return_value={})
    chat_client.create_session = AsyncMock(return_value={"poll_url": "http://p"})
    agent._chat_client = chat_client
    redis = _make_redis()
    await bot_router._run_agent(redis, agent, "s1", "转人工", "m1", "orig-1")
    # 异常路径: transfer_reason 追加降级标记 (通过扩展 response key 断言困难, 验证 xack + setex 即可)
    redis.setex.assert_awaited()
    redis.xack.assert_awaited()


async def test_run_agent_transfer_history_error():
    """转人工 + 历史加载失败 → 空历史继续"""
    agent = _make_agent(_transfer_result())
    sm = agent._session_manager
    sm.get_history = AsyncMock(side_effect=RuntimeError("redis down"))
    chat_client = MagicMock()
    chat_client.build_transfer_request = MagicMock(return_value={})
    chat_client.create_session = AsyncMock(return_value={"pollUrl": "http://p", "sessionId": "s1"})
    agent._chat_client = chat_client
    redis = _make_redis()
    await bot_router._run_agent(redis, agent, "s1", "转人工", "m1", "orig-1")
    chat_client.create_session.assert_awaited_once()


async def test_run_agent_transfer_no_chat_client():
    """转人工 + chat-svc 未初始化 → 警告 + 跳过桥接"""
    agent = _make_agent(_transfer_result())
    agent._chat_client = None
    redis = _make_redis()
    await bot_router._run_agent(redis, agent, "s1", "转人工", "m1", "orig-1")
    redis.setex.assert_awaited()
    redis.xack.assert_awaited()


async def test_run_agent_transfer_response_key_not_existing():
    """转人工 + response key 不存在 → 不扩展 (不报错)"""
    agent = _make_agent(_transfer_result())
    agent._chat_client = None
    redis = _make_redis()  # get 返回 None
    await bot_router._run_agent(redis, agent, "s1", "转人工", "m1", "orig-1")
    redis.setex.assert_awaited()


async def test_run_agent_normal_with_merged_audit():
    """非转人工 + 合并消息 → orig + merged 双审计"""
    agent = _make_agent()
    redis = _make_redis()
    with patch.object(bot_router, "update_chat_message", new=AsyncMock()) as update_msg:
        await bot_router._run_agent(
            redis, agent, "s1", "hi", "m1", "orig-1", merged_message_ids=["mid-2", "mid-3"]
        )
    assert update_msg.await_count == 3
    sources = {c.kwargs.get("source") for c in update_msg.call_args_list}
    assert "merged" in sources
    redis.xack.assert_awaited()
    redis.hdel.assert_awaited()  # 清理重试计数


async def test_run_agent_ended_revival_failure():
    """ENDED 复活失败 → 降级按新会话处理"""
    from datetime import datetime

    from lumio.shared.models import SessionState

    agent = _make_agent()
    sm = agent._session_manager
    state = SessionState(
        session_id="s1",
        customer_id="c1",
        current_phase=SessionPhase.ENDED,
        sub_phase=SessionSubPhase.BOT_ACTIVE,
        created_at=datetime.now(),
        last_active_at=datetime.now(),
        end_reason="timeout",
    )
    sm.get_or_create = AsyncMock(return_value=state)
    sm.transition_phase = AsyncMock(side_effect=RuntimeError("transition failed"))
    redis = _make_redis()
    await bot_router._run_agent(redis, agent, "s1", "hi", "m1", "orig-1")
    redis.setex.assert_awaited()  # 复活失败仍正常回复


# ── _wait_for_response ──────────────────────────────────────────


class _FakePubSub:
    """模拟 Redis pubsub (listen 返回异步生成器)"""

    def __init__(self, messages: list[dict] | None = None, *, listen_value: object | None = None):
        self._messages = messages or []
        self._listen_value = listen_value
        self.subscribed: str | None = None
        self.unsubscribed: list[str] = []
        self.aclosed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed = channel

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def aclose(self) -> None:
        self.aclosed = True

    def listen(self):
        if self._listen_value is not None:
            return self._listen_value

        async def gen():
            for m in self._messages:
                yield m

        return gen()


def _make_pubsub_redis(pubsub: _FakePubSub) -> AsyncMock:
    redis = AsyncMock()
    redis.pubsub = MagicMock(return_value=pubsub)
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    return redis


async def test_wait_for_response_message_ready():
    """收到 message 通知 + response key 就绪 → 返回响应"""
    pubsub = _FakePubSub([{"type": "message"}])
    redis = _make_pubsub_redis(pubsub)
    redis.get = AsyncMock(return_value=json.dumps({"status": "done", "reply": "hi"}))
    result = await bot_router._wait_for_response(redis, "k", "ch", 10)
    assert result.status_code == 200
    assert json.loads(result.body)["reply"] == "hi"
    redis.delete.assert_awaited()
    assert pubsub.aclosed


async def test_wait_for_response_message_late_ready():
    """message 通知后 key 未就绪 → 短暂等待 → 就绪返回"""
    pubsub = _FakePubSub([{"type": "message"}])
    redis = _make_pubsub_redis(pubsub)
    redis.get = AsyncMock(side_effect=[None, json.dumps({"status": "done", "reply": "晚到"})])
    result = await bot_router._wait_for_response(redis, "k", "ch", 10)
    assert json.loads(result.body)["reply"] == "晚到"


async def test_wait_for_response_message_never_ready():
    """message 通知后 key 始终未就绪 → 超时 JSON"""
    pubsub = _FakePubSub([{"type": "message"}])
    redis = _make_pubsub_redis(pubsub)
    redis.get = AsyncMock(return_value=None)
    result = await bot_router._wait_for_response(redis, "k", "ch", 10)
    assert result.status_code == 200
    assert json.loads(result.body)["status"] == "timeout"


async def test_wait_for_response_subscribe_ready():
    """subscribe 确认帧时结果已就绪 → 立即返回"""
    pubsub = _FakePubSub([{"type": "subscribe"}])
    redis = _make_pubsub_redis(pubsub)
    redis.get = AsyncMock(return_value=json.dumps({"status": "done", "reply": "x"}))
    result = await bot_router._wait_for_response(redis, "k", "ch", 10)
    assert json.loads(result.body)["reply"] == "x"


async def test_wait_for_response_elapsed_timeout():
    """等待超过 timeout → key 未就绪 → 超时 JSON"""
    pubsub = _FakePubSub([{"type": "message"}])

    async def gen():
        await asyncio.sleep(1.1)
        yield {"type": "message"}

    pubsub._messages = []
    pubsub._listen_value = gen()
    redis = _make_pubsub_redis(pubsub)
    redis.get = AsyncMock(return_value=None)
    result = await bot_router._wait_for_response(redis, "k", "ch", 1)
    assert json.loads(result.body)["status"] == "timeout"


async def test_wait_for_response_elapsed_ready():
    """等待超过 timeout 但 key 已就绪 → 返回响应"""
    pubsub = _FakePubSub([{"type": "message"}])

    async def gen():
        await asyncio.sleep(1.1)
        yield {"type": "message"}

    pubsub._listen_value = gen()
    redis = _make_pubsub_redis(pubsub)
    redis.get = AsyncMock(return_value=json.dumps({"status": "done", "reply": "慢回复"}))
    result = await bot_router._wait_for_response(redis, "k", "ch", 1)
    assert json.loads(result.body)["reply"] == "慢回复"


async def test_wait_for_response_listener_not_iterable():
    """listen() 不支持异步迭代 (异常环境) → 降级超时"""
    pubsub = _FakePubSub(listen_value=12345)
    redis = _make_pubsub_redis(pubsub)
    result = await bot_router._wait_for_response(redis, "k", "ch", 10)
    assert json.loads(result.body)["status"] == "timeout"
    assert pubsub.aclosed  # 资源仍释放


async def test_wait_for_response_pubsub_coroutine():
    """pubsub() 返回协程 (AsyncMock 环境) → await 后继续"""
    pubsub = _FakePubSub([{"type": "message"}])
    redis = AsyncMock()
    redis.pubsub = AsyncMock(return_value=pubsub)  # 返回协程, 触发 await 分支
    redis.get = AsyncMock(return_value=json.dumps({"status": "done", "reply": "x"}))
    redis.delete = AsyncMock()
    result = await bot_router._wait_for_response(redis, "k", "ch", 10)
    assert json.loads(result.body)["reply"] == "x"
