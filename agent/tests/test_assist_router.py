"""坐席辅助路由 ASGI 单元测试 (assist/router.py, 全 mock)"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lumio.services.assist.router import router as assist_router
from lumio.shared.middleware import register_exception_handlers
from lumio.shared.models import (
    SessionPhase,
    SessionState,
    SessionSubPhase,
)


def _make_state(**kwargs) -> SessionState:
    """构造会话状态"""
    from datetime import datetime

    defaults = dict(
        session_id="s1",
        customer_id="c1",
        current_phase=SessionPhase.AGENT,
        sub_phase=SessionSubPhase.AG_REVIEWING,
        created_at=datetime.now(),
        last_active_at=datetime.now(),
    )
    defaults.update(kwargs)
    return SessionState(**defaults)


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(assist_router, prefix="/api")
    register_exception_handlers(app)
    return app


@pytest.fixture
def setup_state(app: FastAPI) -> tuple[MagicMock, dict]:
    """mock session_manager + redis"""
    sm = MagicMock()
    sm.transition_phase = AsyncMock(return_value=_make_state())
    sm.get_session = AsyncMock(return_value=_make_state())
    sm.patch_state = AsyncMock(return_value={"ok": True, "new_version": 2})
    app.state.session_manager = sm
    app.state.redis_client = AsyncMock()
    app.state.llm_client = None
    app.state.assist_ws_pool = {}
    return sm, {}


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── 健康检查 ──


async def test_health_live(app: FastAPI) -> None:
    """liveness 探针"""
    async with await _client(app) as c:
        resp = await c.get("/api/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


async def test_health_check(app: FastAPI, setup_state) -> None:
    """健康检查含依赖状态"""
    async with await _client(app) as c:
        resp = await c.get("/api/health")
    assert resp.status_code in (200, 503)
    assert resp.json()["service"] == "assist"


async def test_health_ready(app: FastAPI, setup_state) -> None:
    """readiness 探针"""
    async with await _client(app) as c:
        resp = await c.get("/api/health/ready")
    assert resp.status_code in (200, 503)


# ── session/update ──


async def test_session_update_success(app: FastAPI, setup_state) -> None:
    """阶段更新成功"""
    sm, _ = setup_state
    async with await _client(app) as c:
        resp = await c.post(
            "/api/session/update",
            json={"session_id": "s1", "phase": "agent", "sub_phase": "agent:active", "agent_id": "a1"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    sm.transition_phase.assert_awaited_once()


async def test_session_update_invalid_phase(app: FastAPI, setup_state) -> None:
    """无效阶段 → 请求模型 Literal 校验拒绝 (422)"""
    async with await _client(app) as c:
        resp = await c.post("/api/session/update", json={"session_id": "s1", "phase": "bogus"})
    assert resp.status_code == 422


async def test_session_update_no_manager(app: FastAPI) -> None:
    """无 session_manager → 5001"""
    async with await _client(app) as c:
        resp = await c.post("/api/session/update", json={"session_id": "s1", "phase": "agent"})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == 5001


async def test_session_update_ended_cleans_ws(app: FastAPI, setup_state) -> None:
    """ENDED 清理 WS 池"""
    sm, _ = setup_state
    sm.transition_phase = AsyncMock(return_value=_make_state(current_phase=SessionPhase.ENDED, sub_phase=None))
    fake_ws = AsyncMock()
    app.state.assist_ws_pool = {"s1": fake_ws}
    async with await _client(app) as c:
        resp = await c.post("/api/session/update", json={"session_id": "s1", "phase": "ended"})
    assert resp.status_code == 200
    assert "s1" not in app.state.assist_ws_pool
    fake_ws.send_json.assert_awaited_once()


# ── hold / resume ──


async def test_hold_session(app: FastAPI, setup_state) -> None:
    """坐席保持 → AG_ON_HOLD"""
    sm, _ = setup_state
    async with await _client(app) as c:
        resp = await c.post("/api/hold", json={"session_id": "s1", "agent_id": "a1"})
    assert resp.status_code == 200
    assert resp.json()["sub_phase"] == "agent:on_hold"
    kwargs = sm.transition_phase.call_args.kwargs
    assert kwargs["new_sub_phase"] == SessionSubPhase.AG_ON_HOLD


async def test_resume_session(app: FastAPI, setup_state) -> None:
    """坐席恢复 → AG_ACTIVE"""
    import lumio.services.assist.router as ar

    sm, _ = setup_state
    # 清理可能残留的跨 loop 静音检测 task
    ar._silence_tasks.clear()
    ar._silence_watchers.clear()
    async with await _client(app) as c:
        resp = await c.post("/api/resume", json={"session_id": "s1", "agent_id": "a1"})
    assert resp.status_code == 200
    assert resp.json()["sub_phase"] == "agent:active"


# ── review ──


async def test_review_generate_state_validation(app: FastAPI, setup_state) -> None:
    """会话不在审核阶段 → 2001"""
    sm, _ = setup_state
    sm.get_session = AsyncMock(return_value=_make_state(sub_phase=SessionSubPhase.AG_ACTIVE))
    async with await _client(app) as c:
        resp = await c.post("/api/review/generate", json={"session_id": "s1", "agent_id": "a1"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == 2001


async def test_review_generate_session_missing(app: FastAPI, setup_state) -> None:
    """会话不存在 → 2001"""
    sm, _ = setup_state
    sm.get_session = AsyncMock(return_value=None)
    async with await _client(app) as c:
        resp = await c.post("/api/review/generate", json={"session_id": "s1", "agent_id": "a1"})
    assert resp.status_code == 400


# ── feedback ──


async def test_feedback_action_confidence(app: FastAPI, setup_state) -> None:
    """反馈提交 (accept → confidence 1.0)"""
    from lumio.services.assist.router import _action_to_confidence

    assert _action_to_confidence("accept") == 1.0
    assert _action_to_confidence("modify") == 0.5
    assert _action_to_confidence("partial_accept") == 0.3
    assert _action_to_confidence("reject") == 0.0
    assert _action_to_confidence("unknown") == 0.0


async def test_feedback_submit(app: FastAPI, setup_state) -> None:
    """反馈端点可用"""
    app.state.redis_client = AsyncMock()
    async with await _client(app) as c:
        resp = await c.post(
            "/api/feedback",
            json={"session_id": "s1", "agent_id": "a1", "action": "accept"},
        )
    assert resp.status_code in (200, 202, 400)


# ── notify / analyze ──


async def test_notify_message(app: FastAPI, setup_state) -> None:
    """notify: 发布到 session 频道 → 202"""
    redis = app.state.redis_client
    async with await _client(app) as c:
        resp = await c.post(
            "/api/notify",
            json={"session_id": "s1", "message": "客户消息", "event": "customer_message"},
        )
    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"
    redis.publish.assert_awaited_once()


async def test_notify_no_redis(app: FastAPI) -> None:
    """notify: 无 Redis → 5001"""
    async with await _client(app) as c:
        resp = await c.post(
            "/api/notify",
            json={"session_id": "s1", "message": "hi", "event": "customer_message"},
        )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == 5001


async def test_analyze_with_classifier(app: FastAPI, setup_state, monkeypatch) -> None:
    """analyze: 分类器 + 引擎降级链路"""
    import lumio.services.assist.router as ar
    from lumio.shared.models import IntentLabel, IntentResult

    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=(
            IntentResult(primary_intent=IntentLabel.BILL_QUERY, primary_confidence=0.9),
            [],
            MagicMock(),
            "rule",
        )
    )
    app.state.classifier = classifier
    app.state.assist_ws_pool = {}

    async def fake_engine(app, session_id, message, intent, confidence, sentiment=None):
        return {"type": "assist_push", "session_id": session_id, "payload": {"fusion_type": "service_only"}}

    monkeypatch.setattr(ar, "_run_assist_engine", fake_engine)

    async with await _client(app) as c:
        resp = await c.post("/api/analyze", json={"session_id": "s1", "message": "查账单"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok" or "session_id" in data


async def test_analyze_classifier_timeout(app: FastAPI, setup_state, monkeypatch) -> None:
    """analyze: 分类超时 → 默认 FAQ 继续"""
    import lumio.services.assist.router as ar

    classifier = MagicMock()

    async def slow_classify(message):
        await asyncio.sleep(5)

    classifier.classify = slow_classify
    app.state.classifier = classifier
    app.state.assist_ws_pool = {}

    async def fake_engine(app, session_id, message, intent, confidence, sentiment=None):
        return None  # 引擎返回 None → 空 payload 占位

    monkeypatch.setattr(ar, "_run_assist_engine", fake_engine)

    async with await _client(app) as c:
        resp = await c.post("/api/analyze", json={"session_id": "s1", "message": "查账单"})
    assert resp.status_code == 200  # 超时降级仍返回


async def test_analyze_no_classifier(app: FastAPI, setup_state, monkeypatch) -> None:
    """analyze: 无分类器 → 默认 FAQ"""
    import lumio.services.assist.router as ar

    app.state.classifier = None
    app.state.assist_ws_pool = {}

    captured = {}

    async def fake_engine(app, session_id, message, intent, confidence, sentiment=None):
        captured["intent"] = intent
        return None

    monkeypatch.setattr(ar, "_run_assist_engine", fake_engine)

    from lumio.shared.models import IntentLabel

    async with await _client(app) as c:
        resp = await c.post("/api/analyze", json={"session_id": "s1", "message": "你好"})
    assert resp.status_code == 200
    assert captured["intent"] == IntentLabel.FAQ


# ── _silence_detector ───────────────────────────────────────────


async def test_silence_detector_pushes_alert(monkeypatch):
    """保持期间静默超时 → 推送 silence_alert"""
    import lumio.services.assist.router as ar

    ws = AsyncMock()
    app = MagicMock()
    app.state.assist_ws_pool = {"s1": ws}
    _real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    ar._silence_tasks.add("s1")
    await ar._silence_detector(app, "s1", "a1", interval=0.01)
    ws.send_json.assert_awaited_once()
    payload = ws.send_json.await_args.args[0]
    assert payload["type"] == "silence_alert"


async def test_silence_detector_cancelled():
    """任务取消 → 静默返回"""
    import lumio.services.assist.router as ar

    task = asyncio.create_task(ar._silence_detector(MagicMock(), "s1", "a1", interval=60))
    await asyncio.sleep(0)
    task.cancel()
    await task  # 静默返回 (CancelledError 被吞)


async def test_silence_detector_task_removed(monkeypatch):
    """任务已从集合移除 → 不推送"""
    import lumio.services.assist.router as ar

    app = MagicMock()
    app.state.assist_ws_pool = {}
    _real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    await ar._silence_detector(app, "s1", "a1", interval=0.01)  # 集合中无 s1
    # 无异常即通过 (无推送目标)


# ── 反馈缓冲 / 撤销 / 延迟提交 ──────────────────────────────────


async def test_feedback_undo_existing(app: FastAPI, setup_state) -> None:
    """撤销已缓冲的反馈 → undone=True"""
    redis = app.state.redis_client
    redis.delete = AsyncMock(return_value=1)
    async with await _client(app) as c:
        resp = await c.post("/api/feedback/undo", json={"session_id": "s1", "agent_id": "a1", "action": "accept"})
    assert resp.status_code == 200
    assert resp.json()["undone"] is True
    redis.delete.assert_awaited()


async def test_feedback_undo_missing(app: FastAPI, setup_state) -> None:
    """撤销不存在的缓冲 → undone=False"""
    redis = app.state.redis_client
    redis.delete = AsyncMock(return_value=0)
    async with await _client(app) as c:
        resp = await c.post("/api/feedback/undo", json={"session_id": "s1", "agent_id": "a1", "action": "accept"})
    assert resp.status_code == 200
    assert resp.json()["undone"] is False


async def test_feedback_undo_no_redis(app: FastAPI) -> None:
    """无 Redis → undone=False + reason"""
    async with await _client(app) as c:
        resp = await c.post("/api/feedback/undo", json={"session_id": "s1", "agent_id": "a1", "action": "accept"})
    assert resp.status_code == 200
    assert resp.json()["reason"] == "not_buffered"


async def test_commit_feedback_after_delay_commits(monkeypatch):
    """延迟提交: 缓冲存在 → patch_state 写入 last_feedback"""
    import lumio.services.assist.router as ar

    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"action": "accept"}')
    redis.delete = AsyncMock(return_value=1)
    sm = MagicMock()
    sm.read_state = AsyncMock(return_value={"version": 3, "session_id": "s1"})
    sm.patch_state = AsyncMock(return_value={"ok": True})
    app = MagicMock()
    app.state.redis_client = redis
    app.state.session_manager = sm
    _real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    await ar._commit_feedback_after_delay(app, "s1", "lumio:feedback:s1:a1", {"action": "accept", "agent_id": "a1"})
    sm.patch_state.assert_awaited_once()
    assert sm.patch_state.await_args.kwargs["patches"]["last_feedback"]["action"] == "accept"
    assert sm.patch_state.await_args.kwargs["expected_version"] == 3
    assert sm.patch_state.await_args.kwargs["writer"] == "feedback:a1"


async def test_commit_feedback_after_delay_undone(monkeypatch):
    """缓冲已被撤销 → 不提交"""
    import lumio.services.assist.router as ar

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # 已被撤销
    sm = MagicMock()
    sm.patch_state = AsyncMock()
    app = MagicMock()
    app.state.redis_client = redis
    app.state.session_manager = sm
    _real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    await ar._commit_feedback_after_delay(app, "s1", "k", {})
    sm.patch_state.assert_not_awaited()


async def test_commit_feedback_after_delay_no_redis(monkeypatch):
    """无 Redis → 直接返回"""
    import lumio.services.assist.router as ar

    app = MagicMock()
    app.state = None
    await ar._commit_feedback_after_delay(app, "s1", "k", {})


async def test_commit_feedback_after_delay_no_manager(monkeypatch):
    """无 session_manager → 直接返回"""
    import lumio.services.assist.router as ar

    redis = AsyncMock()
    redis.get = AsyncMock(return_value="{}")
    app = MagicMock()
    app.state.redis_client = redis
    app.state.session_manager = None
    _real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    await ar._commit_feedback_after_delay(app, "s1", "k", {})


async def test_commit_feedback_after_delay_exception(monkeypatch):
    """read_state 异常 → 记日志不抛错"""
    import lumio.services.assist.router as ar

    redis = AsyncMock()
    redis.get = AsyncMock(return_value="{}")
    sm = MagicMock()
    sm.read_state = AsyncMock(side_effect=RuntimeError("boom"))
    app = MagicMock()
    app.state.redis_client = redis
    app.state.session_manager = sm
    _real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    await ar._commit_feedback_after_delay(app, "s1", "k", {})


# ── _load_sentiment_history / _handle_messages / _process_customer_message ──


async def test_load_sentiment_history(monkeypatch):
    """加载客户情绪历史"""
    import lumio.services.assist.router as ar
    from lumio.shared.models import SentimentLabel

    turn_c = MagicMock(speaker="customer", emotion_label=SentimentLabel.POSITIVE)
    turn_b = MagicMock(speaker="agent", emotion_label=SentimentLabel.NEGATIVE)
    sm = MagicMock()
    sm.get_session = AsyncMock(return_value=MagicMock(turns=[turn_c, turn_b]))
    history = await ar._load_sentiment_history(sm, "s1")
    assert history == [SentimentLabel.POSITIVE]  # 仅客户情绪


async def test_load_sentiment_history_error(monkeypatch):
    """加载失败 → 空列表 + 不抛错"""
    import lumio.services.assist.router as ar

    sm = MagicMock()
    sm.get_session = AsyncMock(side_effect=RuntimeError("redis down"))
    history = await ar._load_sentiment_history(sm, "s1")
    assert history == []


async def test_handle_messages_ping_text(monkeypatch):
    """裸 ping → pong 文本"""
    import lumio.services.assist.router as ar

    ws = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=["ping", TimeoutError()])
    _real_wait_for = asyncio.wait_for

    async def fast_wait_for(coro, timeout):
        try:
            return await _real_wait_for(coro, timeout=0.001)
        except TimeoutError:
            raise TimeoutError from None

    monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)
    with pytest.raises(asyncio.TimeoutError):
        await ar._handle_messages(ws, MagicMock(), "s1", [])
    ws.send_text.assert_awaited_once_with("pong")


async def test_handle_messages_invalid_json(monkeypatch):
    """非法 JSON → error 事件"""
    import lumio.services.assist.router as ar

    ws = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=["not-json", TimeoutError()])
    _real_wait_for = asyncio.wait_for

    async def fast_wait_for(coro, timeout):
        try:
            return await _real_wait_for(coro, timeout=0.001)
        except TimeoutError:
            raise TimeoutError from None

    monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)
    with pytest.raises(asyncio.TimeoutError):
        await ar._handle_messages(ws, MagicMock(), "s1", [])
    ws.send_json.assert_awaited_once()
    assert ws.send_json.await_args.args[0]["type"] == "error"


async def test_handle_messages_ping_json(monkeypatch):
    """JSON ping → pong 事件"""
    import json as _json

    import lumio.services.assist.router as ar

    ws = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=[_json.dumps({"type": "ping"}), TimeoutError()])
    _real_wait_for = asyncio.wait_for

    async def fast_wait_for(coro, timeout):
        try:
            return await _real_wait_for(coro, timeout=0.001)
        except TimeoutError:
            raise TimeoutError from None

    monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)
    with pytest.raises(asyncio.TimeoutError):
        await ar._handle_messages(ws, MagicMock(), "s1", [])
    assert ws.send_json.await_args.args[0]["type"] == "pong"


async def test_process_customer_message_push(monkeypatch):
    """客户消息 → 引擎推送 + 情绪记录"""
    import lumio.services.assist.router as ar
    from lumio.shared.models import SentimentLabel

    ws = AsyncMock()
    app = MagicMock()
    ar._run_assist_engine = AsyncMock(return_value={"type": "assist_push", "payload": {"x": 1}})
    history: list = []
    await ar._process_customer_message(ws, app, "s1", {"message": "你好", "intent": "faq", "sentiment": "positive"}, history)
    ws.send_json.assert_awaited_once()
    assert history == [SentimentLabel.POSITIVE]
    del ar._run_assist_engine  # 还原 (直接删除注入)


async def test_process_customer_message_throttled(monkeypatch):
    """引擎无输出 → 不推送"""
    import lumio.services.assist.router as ar

    ws = AsyncMock()
    app = MagicMock()
    ar._run_assist_engine = AsyncMock(return_value=None)
    await ar._process_customer_message(ws, app, "s1", {"message": "hi"}, [])
    ws.send_json.assert_not_awaited()
    del ar._run_assist_engine


async def test_process_customer_message_invalid_intent(monkeypatch):
    """非法 intent/sentiment → 回退 FAQ/NEUTRAL"""
    import lumio.services.assist.router as ar
    from lumio.shared.models import IntentLabel

    ws = AsyncMock()
    app = MagicMock()
    captured = {}

    async def fake_engine(app_, **kwargs):
        captured.update(kwargs)

    ar._run_assist_engine = fake_engine
    await ar._process_customer_message(
        ws, app, "s1", {"message": "hi", "intent": "not-an-intent", "sentiment": "weird"}, []
    )
    assert captured["intent"] == IntentLabel.FAQ
    del ar._run_assist_engine


# ── _handle_agent_messages (坐席 WS) ────────────────────────────


class _IterTextWS:
    """模拟坐席 WS: iter_text 返回固定消息列表"""

    def __init__(self, messages: list[str]):
        self._messages = list(messages)
        self.sent: list[dict] = []

    async def iter_text(self):
        for m in self._messages:
            yield m
        await asyncio.sleep(3600)  # 挂起直到取消

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


async def test_agent_messages_session_activated(monkeypatch):
    """坐席接听 AG_QUEUED 会话 → 激活 + assist_ready 摘要推送"""
    import json as _json

    import lumio.services.assist.router as ar

    state = _make_state(sub_phase=SessionSubPhase.AG_QUEUED)
    state.transfer_reason = "客户要求"
    state.transfer_summary = "摘要"
    state.vip_level = "gold"
    state.card_types = ["visa"]
    state.last_entities = [MagicMock(entity_type="card", value="1234")]
    state.turns = [MagicMock(speaker="customer", content="你好")]

    sm = MagicMock()
    sm.get_session = AsyncMock(return_value=state)
    sm.transition_phase = AsyncMock(return_value=state)
    app = MagicMock()
    app.state.session_manager = sm

    ws = _IterTextWS([_json.dumps({"type": "session_activated", "session_id": "s1"})])
    task = asyncio.create_task(ar._handle_agent_messages(ws, app, "a1"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    sm.transition_phase.assert_awaited_once()
    assert sm.transition_phase.await_args.kwargs["new_sub_phase"] == SessionSubPhase.AG_ACTIVE
    assert ws.sent
    assert ws.sent[0]["type"] == "assist_ready"
    assert ws.sent[0]["summary"]["vip_level"] == "gold"


async def test_agent_messages_session_activated_not_queued(monkeypatch):
    """非 AG_QUEUED 状态 → 不激活 (transition 不调用)"""
    import json as _json

    import lumio.services.assist.router as ar

    state = _make_state(sub_phase=SessionSubPhase.AG_ACTIVE)
    sm = MagicMock()
    sm.get_session = AsyncMock(return_value=state)
    sm.transition_phase = AsyncMock(return_value=state)
    app = MagicMock()
    app.state.session_manager = sm
    ws = _IterTextWS([_json.dumps({"type": "session_activated", "session_id": "s1"})])
    task = asyncio.create_task(ar._handle_agent_messages(ws, app, "a1"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    sm.transition_phase.assert_not_awaited()


async def test_agent_messages_session_activated_transition_error(monkeypatch):
    """transition 异常 (SessionNotFound) → 跳过"""
    import json as _json

    import lumio.services.assist.router as ar
    from lumio.shared.exceptions import SessionNotFoundError

    state = _make_state(sub_phase=SessionSubPhase.AG_QUEUED)
    sm = MagicMock()
    sm.get_session = AsyncMock(return_value=state)
    sm.transition_phase = AsyncMock(side_effect=SessionNotFoundError())
    app = MagicMock()
    app.state.session_manager = sm
    ws = _IterTextWS([_json.dumps({"type": "session_activated", "session_id": "s1"})])
    task = asyncio.create_task(ar._handle_agent_messages(ws, app, "a1"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # 无 assist_ready 推送 (异常被吞)
    assert not any(m.get("type") == "assist_ready" for m in ws.sent)


async def test_agent_messages_agent_message_alerts(monkeypatch):
    """坐席回复 → 合规检测 → assist_push 告警推送"""
    import json as _json

    import lumio.services.assist.router as ar

    alert = MagicMock(level="high", category="compliance", message="敏感词", rule_id="R1")
    alert_engine = MagicMock()
    alert_engine.check = AsyncMock(return_value=[alert])
    app = MagicMock()
    app.state.alert_engine = alert_engine
    app.state.script_service = MagicMock(_scripts_cache=[])

    ws = _IterTextWS([_json.dumps({"type": "agent_message", "session_id": "s1", "content": "回复内容"})])
    with patch.object(ar, "_infer_feedback") as infer:
        task = asyncio.create_task(ar._handle_agent_messages(ws, app, "a1"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    alert_engine.check.assert_awaited_once()
    assert any(
        m.get("type") == "assist_push" and m["payload"]["alerts"][0]["level"] == "high" for m in ws.sent
    )
    infer.assert_called_once()


async def test_agent_messages_invalid_json(monkeypatch):
    """非法 JSON → 忽略继续"""
    import lumio.services.assist.router as ar

    app = MagicMock()
    app.state.session_manager = MagicMock()
    ws = _IterTextWS(["not-json"])
    task = asyncio.create_task(ar._handle_agent_messages(ws, app, "a1"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # 无异常即通过


# ── _infer_feedback ──


async def test_infer_feedback_accept(monkeypatch):
    """坐席回复与话术高度相似 → 推断 accept (任务完成无异常)"""
    import lumio.services.assist.router as ar

    app = MagicMock()
    script = MagicMock()
    script.get = lambda k, d=None: "尊敬的客户您好" if k == "content" else d
    app.state.script_service = MagicMock(_scripts_cache=[script])
    ar._infer_feedback(app, "s1", "尊敬的客户您好")
    await asyncio.sleep(0.05)  # 等待后台任务
    assert ar._feedback_tasks


async def test_infer_feedback_no_script_service():
    """无话术库 → 直接返回"""
    import lumio.services.assist.router as ar

    app = MagicMock()
    app.state.script_service = None
    ar._infer_feedback(app, "s1", "内容")
    await asyncio.sleep(0.01)


# ── start/stop_notify_worker ──


async def test_notify_worker_lifecycle():
    """notify worker 启动/停止 (兼容占位)"""
    import lumio.services.assist.router as ar

    await ar.start_notify_worker(MagicMock())
    await ar.stop_notify_worker(MagicMock())


# ── kb/search ──


async def test_kb_search(app: FastAPI, setup_state, monkeypatch):
    """坐席知识库搜索 (全依赖 mock)"""
    from lumio.services.common import deps

    fake_result = {"results": [{"doc_id": "d1", "content": "年费说明", "score": 0.9}]}
    with patch.object(deps, "get_es_client", return_value=None), patch.object(
        deps, "get_milvus_collection", return_value=None
    ), patch.object(deps, "get_embedding_breaker", return_value=MagicMock(is_available=False, provider=None)), patch.object(
        deps, "get_reranker_provider", return_value=None
    ), patch.object(deps, "get_es_breaker", return_value=MagicMock(allow_request=MagicMock(return_value=True))), patch.object(
        deps, "get_milvus_breaker", return_value=MagicMock(allow_request=MagicMock(return_value=True))
    ), patch("lumio.services.common.retrieval.retrieve", new=AsyncMock(return_value=fake_result)):
        async with await _client(app) as c:
            resp = await c.post("/api/kb/search", json={"query": "年费", "top_k": 3})
    assert resp.status_code == 200
    assert resp.json()["results"][0]["doc_id"] == "d1"


# ── transfer-to-bot ──


async def test_transfer_to_bot(app: FastAPI, setup_state) -> None:
    """转回 Bot: 状态迁移 + WS 通知"""
    sm = setup_state[0]
    app.state.assist_ws_pool = {"s1": AsyncMock()}
    ws = app.state.assist_ws_pool["s1"]
    async with await _client(app) as c:
        resp = await c.post("/api/transfer-to-bot", json={"session_id": "s1", "agent_id": "a1", "reason": "已解决"})
    assert resp.status_code == 200
    assert resp.json()["transferred_to"] == "bot"
    sm.transition_phase.assert_awaited_once()
    assert sm.transition_phase.await_args.kwargs["new_phase"] == SessionPhase.BOT
    ws.send_json.assert_awaited_once()
    assert ws.send_json.await_args.args[0]["type"] == "session_transferred"


async def test_transfer_to_bot_no_manager(app: FastAPI) -> None:
    """无 session_manager → 5001"""
    async with await _client(app) as c:
        resp = await c.post("/api/transfer-to-bot", json={"session_id": "s1", "agent_id": "a1"})
    assert resp.status_code == 500


async def test_transfer_to_bot_no_ws(app: FastAPI, setup_state) -> None:
    """无 WS 连接 → 跳过通知"""
    sm = setup_state[0]
    app.state.assist_ws_pool = {}
    async with await _client(app) as c:
        resp = await c.post("/api/transfer-to-bot", json={"session_id": "s1", "agent_id": "a1"})
    assert resp.status_code == 200
    sm.transition_phase.assert_awaited_once()


# ── session_websocket / assist_websocket / _handle_ws_notify ────


class _WSEndpoint:
    """模拟 FastAPI WebSocket (含 .app)"""

    def __init__(self, app):
        self.app = app
        self.accepted = False
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


async def test_session_websocket_ready_and_cleanup(monkeypatch):
    """session WS: accept + assist_ready + 连接池注册/清理"""
    from starlette.websockets import WebSocketDisconnect

    import lumio.services.assist.router as ar

    app = MagicMock()
    app.state.session_manager = MagicMock(get_session=AsyncMock(return_value=MagicMock(turns=[])))
    app.state.assist_ws_pool = {}
    ws = _WSEndpoint(app)

    async def fake_messages(*a, **kw):
        raise WebSocketDisconnect()

    async def fake_notify(*a, **kw):
        await asyncio.sleep(3600)

    with patch.object(ar, "_handle_messages", new=fake_messages), patch.object(
        ar, "_handle_ws_notify", new=fake_notify
    ):
        await ar.session_websocket(ws, "s1")
    assert ws.accepted
    assert ws.sent[0]["type"] == "assist_ready"
    assert "s1" not in app.state.assist_ws_pool  # 已清理


async def test_assist_websocket_connected_and_cleanup(monkeypatch):
    """agent WS: accept + connected + 心跳清理 + 池清理"""
    from starlette.websockets import WebSocketDisconnect

    import lumio.services.assist.router as ar

    app = MagicMock()
    app.state.assist_ws_pool = {}
    ws = _WSEndpoint(app)

    async def fake_handle(*a, **kw):
        raise WebSocketDisconnect()

    with patch.object(ar, "_handle_agent_messages", new=fake_handle):
        await ar.assist_websocket(ws, "a1")
    assert ws.accepted
    assert ws.sent[0]["type"] == "connected"
    assert "a1" not in app.state.assist_ws_pool


async def test_handle_ws_notify_message(monkeypatch):
    """notify 消息 → _process_notify_message 处理"""
    import json as _json

    import lumio.services.assist.router as ar

    class _PubSub:
        def __init__(self):
            self.msgs = [{"type": "message", "data": _json.dumps({"session_id": "s1", "content": "hi"})}]
            self.unsubscribed = False
            self.aclosed = False

        async def subscribe(self, ch):
            pass

        async def unsubscribe(self, ch):
            self.unsubscribed = True

        async def aclose(self):
            self.aclosed = True

        def listen(self):
            async def gen():
                for m in self.msgs:
                    yield m
                await asyncio.sleep(3600)

            return gen()

    pubsub = _PubSub()
    redis = AsyncMock()
    redis.pubsub = MagicMock(return_value=pubsub)
    app = MagicMock()
    app.state.redis_client = redis
    ws = AsyncMock()
    with patch.object(ar, "_process_notify_message", new=AsyncMock()) as proc:
        task = asyncio.create_task(ar._handle_ws_notify(ws, app, "s1"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    proc.assert_awaited_once()


async def test_handle_ws_notify_no_redis():
    """无 Redis → 直接返回"""
    import lumio.services.assist.router as ar

    app = MagicMock()
    app.state.redis_client = None
    await ar._handle_ws_notify(AsyncMock(), app, "s1")


async def test_handle_ws_notify_invalid_json(monkeypatch):
    """notify 消息非法 JSON → 跳过"""
    import lumio.services.assist.router as ar

    class _PubSub:
        async def subscribe(self, ch):
            pass

        def listen(self):
            async def gen():
                yield {"type": "message", "data": "not-json"}
                await asyncio.sleep(3600)

            return gen()

    redis = AsyncMock()
    redis.pubsub = MagicMock(return_value=_PubSub())
    app = MagicMock()
    app.state.redis_client = redis
    with patch.object(ar, "_process_notify_message", new=AsyncMock()) as proc:
        task = asyncio.create_task(ar._handle_ws_notify(AsyncMock(), app, "s1"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    proc.assert_not_awaited()


async def test_heartbeat_cancelled(monkeypatch):
    """心跳循环可取消"""
    import lumio.services.assist.router as ar

    _real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await _real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    task = asyncio.create_task(ar._heartbeat(AsyncMock(), interval=0.01))
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── record_feedback PushTracker 联动 ────────────────────────────


async def test_feedback_with_card_type_tracker(app: FastAPI, setup_state) -> None:
    """反馈带 card_type → PushTracker 联动 (新建 tracker)"""
    redis = app.state.redis_client
    redis.get = AsyncMock(return_value=None)
    async with await _client(app) as c:
        resp = await c.post(
            "/api/feedback",
            json={"session_id": "s1", "agent_id": "a1", "action": "accept", "card_type": "ai"},
        )
    assert resp.status_code == 200
    assert resp.json()["confidence"] == 1.0
    # tracker 写入 (setex 最后一次调用)
    tracker_calls = [c for c in redis.setex.call_args_list if "tracker" in str(c.args[0])]
    assert tracker_calls


async def test_feedback_with_card_type_tracker_existing(app: FastAPI, setup_state) -> None:
    """已有 tracker → 加载 + 更新"""
    import json as _json

    redis = app.state.redis_client
    redis.get = AsyncMock(return_value=_json.dumps({"min_interval": {"visa": 60}, "events": []}))
    async with await _client(app) as c:
        resp = await c.post(
            "/api/feedback",
            json={"session_id": "s1", "agent_id": "a1", "action": "modify", "card_type": "ai"},
        )
    assert resp.status_code == 200
    assert resp.json()["confidence"] == 0.5


async def test_feedback_tracker_update_error(app: FastAPI, setup_state) -> None:
    """PushTracker 更新异常 → 不影响反馈"""
    redis = app.state.redis_client
    redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
    async with await _client(app) as c:
        resp = await c.post(
            "/api/feedback",
            json={"session_id": "s1", "agent_id": "a1", "action": "reject", "card_type": "ai"},
        )
    assert resp.status_code == 200
    assert resp.json()["confidence"] == 0.0


async def test_feedback_no_redis(app: FastAPI) -> None:
    """无 Redis → 仍启动延迟提交 (状态管理器路径)"""
    async with await _client(app) as c:
        resp = await c.post("/api/feedback", json={"session_id": "s1", "agent_id": "a1", "action": "accept"})
    assert resp.status_code == 200
    assert resp.json()["delayed_commit"] is True
