"""坐席辅助路由 ASGI 单元测试 (assist/router.py, 全 mock)"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

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
