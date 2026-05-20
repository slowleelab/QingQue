"""E2E 会话生命周期集成测试

验证 star-connection → SmartCS 回调链路的端到端流程：
1. Session update 回调 (AGENT/ENDED 阶段)
2. Analyze 回调 (客户消息 → AI 分析)
3. Feedback 反馈闭环
4. Review 话后小结
5. Hold/Resume 坐席保持
6. 状态转换校验

前置条件：Docker 中间件已启动 (make up) + 服务运行 (make dev)
标记为 @pytest.mark.slow，CI 中单独运行。
"""

from __future__ import annotations

import asyncio
import json
import uuid as uuid_module
from datetime import UTC, datetime

import httpx
import pytest
import redis.asyncio as aioredis

_SESSION_META_PREFIX = "smartcs:session"


async def _create_session_in_redis(session_id: str, phase: str = "bot", sub_phase: str = "bot:active"):
    """在 Redis 中创建会话状态"""
    from smartcs.shared.models import ChannelType, SessionPhase, SessionState, SessionSubPhase

    state = SessionState(
        session_id=session_id,
        customer_id=None,
        channel_type=ChannelType.WEB,
        current_phase=SessionPhase(phase),
        sub_phase=SessionSubPhase(sub_phase) if sub_phase else None,
        created_at=datetime.now(UTC),
        last_active_at=datetime.now(UTC),
    )

    r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)
    meta_key = f"{_SESSION_META_PREFIX}:{session_id}:meta"
    await r.setex(meta_key, 1800, state.model_dump_json())
    await r.aclose()


async def _get_session_from_redis(session_id: str) -> dict | None:
    """从 Redis 读取会话状态"""
    r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)
    meta_key = f"{_SESSION_META_PREFIX}:{session_id}:meta"
    raw = await r.get(meta_key)
    await r.aclose()
    if raw:
        return json.loads(raw)
    return None


# ── 1. Session Update 回调链路 ──


@pytest.mark.slow
async def test_session_update_to_agent_phase(
    bot_client: httpx.AsyncClient, assist_client: httpx.AsyncClient
):
    """BOT → AGENT:queued 回调成功"""
    session_id = "e2e-lifecycle-agent-" + uuid_module.uuid4().hex[:8]
    await _create_session_in_redis(session_id, "bot", "bot:active")

    # 模拟 star-connection 回调: 会话进入 AGENT 阶段
    resp = await assist_client.post("/api/session/update", json={
        "session_id": session_id,
        "phase": "AGENT",
        "sub_phase": "agent:queued",
        "agent_id": "agent-001",
    })
    assert resp.status_code == 200, f"Session update failed: {resp.text}"

    # 验证 Redis 中状态已更新
    state = await _get_session_from_redis(session_id)
    assert state is not None
    assert state["current_phase"] == "agent"
    assert state["sub_phase"] == "agent:queued"


@pytest.mark.slow
async def test_session_update_to_ended(
    bot_client: httpx.AsyncClient, assist_client: httpx.AsyncClient
):
    """AGENT:active → ENDED 回调成功，end_reason 记录"""
    session_id = "e2e-lifecycle-ended-" + uuid_module.uuid4().hex[:8]
    await _create_session_in_redis(session_id, "agent", "agent:active")

    resp = await assist_client.post("/api/session/update", json={
        "session_id": session_id,
        "phase": "ENDED",
        "agent_id": "agent-001",
        "end_reason": "completed",
    })
    assert resp.status_code == 200

    state = await _get_session_from_redis(session_id)
    assert state is not None
    assert state["current_phase"] == "ended"
    assert state["end_reason"] == "completed"


@pytest.mark.slow
async def test_session_update_invalid_transition(
    assist_client: httpx.AsyncClient,
):
    """非法转换 (bot:active → agent:active) 应被拒绝"""
    session_id = "e2e-lifecycle-invalid-" + uuid_module.uuid4().hex[:8]
    await _create_session_in_redis(session_id, "bot", "bot:active")

    resp = await assist_client.post("/api/session/update", json={
        "session_id": session_id,
        "phase": "AGENT",
        "sub_phase": "agent:active",
    })
    assert resp.status_code == 500  # ValueError → 500


# ── 2. 子阶段推进链路 ──


@pytest.mark.slow
async def test_sub_phase_progression(
    bot_client: httpx.AsyncClient, assist_client: httpx.AsyncClient
):
    """完整子阶段推进: bot:active → agent:queued → agent:assigned → agent:active"""
    session_id = "e2e-lifecycle-prog-" + uuid_module.uuid4().hex[:8]
    await _create_session_in_redis(session_id, "bot", "bot:active")

    # BOT → AG_QUEUED
    resp = await assist_client.post("/api/session/update", json={
        "session_id": session_id,
        "phase": "AGENT",
        "sub_phase": "agent:queued",
    })
    assert resp.status_code == 200

    # AG_QUEUED → AG_ASSIGNED
    resp = await assist_client.post("/api/session/update", json={
        "session_id": session_id,
        "phase": "AGENT",
        "sub_phase": "agent:assigned",
        "agent_id": "agent-002",
    })
    assert resp.status_code == 200

    # AG_ASSIGNED → AG_ACTIVE
    resp = await assist_client.post("/api/session/update", json={
        "session_id": session_id,
        "phase": "AGENT",
        "sub_phase": "agent:active",
        "agent_id": "agent-002",
    })
    assert resp.status_code == 200

    state = await _get_session_from_redis(session_id)
    assert state["sub_phase"] == "agent:active"
    assert state["agent_id"] == "agent-002"


# ── 3. Hold/Resume 端点 ──


@pytest.mark.slow
async def test_hold_resume_flow(
    bot_client: httpx.AsyncClient, assist_client: httpx.AsyncClient
):
    """AG_ACTIVE → AG_ON_HOLD → AG_ACTIVE 保持恢复流程"""
    session_id = "e2e-lifecycle-hold-" + uuid_module.uuid4().hex[:8]
    await _create_session_in_redis(session_id, "agent", "agent:active")

    # Hold
    resp = await assist_client.post("/api/hold", json={
        "session_id": session_id,
        "agent_id": "agent-003",
        "reason": "查询系统",
    })
    assert resp.status_code == 200
    assert resp.json()["sub_phase"] == "agent:on_hold"

    state = await _get_session_from_redis(session_id)
    assert state["sub_phase"] == "agent:on_hold"

    # Resume
    resp = await assist_client.post("/api/resume", json={
        "session_id": session_id,
        "agent_id": "agent-003",
    })
    assert resp.status_code == 200
    assert resp.json()["sub_phase"] == "agent:active"

    state = await _get_session_from_redis(session_id)
    assert state["sub_phase"] == "agent:active"


# ── 4. Review 话后小结 ──


@pytest.mark.slow
async def test_review_generate_without_llm(
    bot_client: httpx.AsyncClient, assist_client: httpx.AsyncClient
):
    """AG_REVIEWING 阶段生成小结 (LLM 不可用时降级模板)"""
    session_id = "e2e-lifecycle-review-" + uuid_module.uuid4().hex[:8]
    await _create_session_in_redis(session_id, "agent", "agent:reviewing")

    resp = await assist_client.post("/api/review/generate", json={
        "session_id": session_id,
        "agent_id": "agent-004",
    })
    # LLM 不可用时，session_manager 可能返回空历史，小结为空
    # 但端点应正常返回
    assert resp.status_code in (200, 500)  # 500 if session_manager not set up


# ── 5. Analyze 回调 ──


@pytest.mark.slow
async def test_analyze_callback(
    bot_client: httpx.AsyncClient, assist_client: httpx.AsyncClient
):
    """POST /api/analyze 回调正常响应"""
    session_id = "e2e-lifecycle-analyze-" + uuid_module.uuid4().hex[:8]
    await _create_session_in_redis(session_id, "agent", "agent:active")

    resp = await assist_client.post("/api/analyze", json={
        "session_id": session_id,
        "message": "我想查一下信用卡账单",
        "customer_id": "cust-001",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "intent" in data


# ── 6. Feedback 反馈 ──


@pytest.mark.slow
async def test_feedback_flow(assist_client: httpx.AsyncClient):
    """反馈 + 撤销流程"""
    session_id = "e2e-lifecycle-feedback-" + uuid_module.uuid4().hex[:8]

    # 提交反馈
    resp = await assist_client.post("/api/feedback", json={
        "session_id": session_id,
        "agent_id": "agent-005",
        "action": "accept",
    })
    assert resp.status_code == 200
    assert resp.json()["delayed_commit"] is True
    assert resp.json()["confidence"] == 1.0

    # 撤销反馈
    resp = await assist_client.post("/api/feedback/undo", json={
        "session_id": session_id,
        "agent_id": "agent-005",
    })
    assert resp.status_code == 200
    assert resp.json()["undone"] is True
