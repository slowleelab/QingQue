"""坐席辅助服务 API 端到端测试

启动真实 uvicorn 服务器（assist :8766），通过 HTTP/WebSocket 请求验证完整链路。

前置条件：Docker 中间件已启动（make up）
"""

from __future__ import annotations

import asyncio
import json
import uuid as uuid_module
from datetime import UTC, datetime

import httpx
import pytest
import redis.asyncio as aioredis
import websockets

# ── Helpers ──


_SESSION_META_PREFIX = "smartcs:session"


async def _create_session_in_redis(session_id: str, phase: str = "bot"):
    """直接在 Redis 中创建会话状态（绕过 bot worker 的缺失 get_or_create 调用）"""
    from smartcs.shared.models import ChannelType, SessionPhase, SessionSubPhase, SessionState

    state = SessionState(
        session_id=session_id,
        customer_id=None,
        channel_type=ChannelType.WEB,
        current_phase=SessionPhase(phase),
        created_at=datetime.now(UTC),
        last_active_at=datetime.now(UTC),
    )

    r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)
    meta_key = f"{_SESSION_META_PREFIX}:{session_id}:meta"
    await r.setex(meta_key, 1800, state.model_dump_json())
    await r.aclose()


async def _ensure_session_exists(bot_client: httpx.AsyncClient, session_id: str):
    """Helper: 通过 Bot 发送消息，然后在 Redis 中创建 session"""
    send_resp = await bot_client.post("/api/chat/send", json={
        "message": "你好",
        "session_id": session_id,
    })
    assert send_resp.status_code == 200

    # 等待 worker 处理消息
    await asyncio.sleep(3)

    # 在 Redis 中创建 session（当前 Agent 不调用 get_or_create，需要手动创建）
    await _create_session_in_redis(session_id, phase="bot")


# ── Health Check ──


async def test_health_check(assist_client: httpx.AsyncClient):
    """GET /api/health 返回 healthy 状态"""
    response = await assist_client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "assist"


async def test_health_check_both_services(
    bot_client: httpx.AsyncClient, assist_client: httpx.AsyncClient
):
    """两个服务同时可达，互不影响"""
    bot_resp = await bot_client.get("/api/health")
    assist_resp = await assist_client.get("/api/health")
    assert bot_resp.status_code == 200
    assert assist_resp.status_code == 200
    assert bot_resp.json()["service"] == "bot"
    assert assist_resp.json()["service"] == "assist"


# ── Session Update ──


async def test_session_update_success(
    bot_client: httpx.AsyncClient, assist_client: httpx.AsyncClient
):
    """POST /api/session/update 成功更新会话状态

    流程：先通过 Bot 聊天创建 session，再通过 Assist 更新其状态。
    """
    session_id = "e2e-assist-session-" + uuid_module.uuid4().hex[:8]
    await _ensure_session_exists(bot_client, session_id)

    # 更新会话状态为 AGENT（star-connection 回调）
    resp = await assist_client.post("/api/session/update", json={
        "session_id": session_id,
        "phase": "AGENT",
    })
    assert resp.status_code == 200, f"Session update failed: {resp.text}"
    data = resp.json()
    assert data["status"] == "ok"


async def test_session_update_lowercase_phase(
    bot_client: httpx.AsyncClient, assist_client: httpx.AsyncClient
):
    """POST /api/session/update 接受小写 phase 值"""
    session_id = "e2e-assist-lower-" + uuid_module.uuid4().hex[:8]
    await _ensure_session_exists(bot_client, session_id)

    resp = await assist_client.post("/api/session/update", json={
        "session_id": session_id,
        "phase": "agent",
    })
    assert resp.status_code == 200


async def test_session_update_ended_phase(
    bot_client: httpx.AsyncClient, assist_client: httpx.AsyncClient
):
    """POST /api/session/update 结束会话"""
    session_id = "e2e-assist-ended-" + uuid_module.uuid4().hex[:8]
    await _ensure_session_exists(bot_client, session_id)

    resp = await assist_client.post("/api/session/update", json={
        "session_id": session_id,
        "phase": "ENDED",
    })
    assert resp.status_code == 200


async def test_session_update_invalid_phase(assist_client: httpx.AsyncClient):
    """POST /api/session/update 无效 phase 值返回 422（Pydantic 校验失败）"""
    resp = await assist_client.post("/api/session/update", json={
        "session_id": "test-invalid-phase",
        "phase": "INVALID_PHASE",
    })
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["code"] == 2000  # RequestValidationError


async def test_session_update_missing_fields(assist_client: httpx.AsyncClient):
    """POST /api/session/update 缺少必填字段返回 422"""
    resp = await assist_client.post("/api/session/update", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == 2000


async def test_session_update_nonexistent_session(assist_client: httpx.AsyncClient):
    """POST /api/session/update 不存在的 session 返回 500（业务逻辑异常）"""
    session_id = "e2e-nonexist-" + uuid_module.uuid4().hex[:8]
    resp = await assist_client.post("/api/session/update", json={
        "session_id": session_id,
        "phase": "AGENT",
    })
    # Session 不存在时会触发 ValueError → 500
    assert resp.status_code == 500


# ── WebSocket ──


ASSIST_WS_PORT = 8766  # 与 conftest 中的 ASSIST_PORT 保持一致


async def test_websocket_connect_and_ready(assist_server: str):
    """WebSocket 连接成功并接收 assist_ready 消息"""
    session_id = "e2e-ws-ready-" + uuid_module.uuid4().hex[:8]
    ws_url = f"ws://127.0.0.1:{ASSIST_WS_PORT}/api/ws/{session_id}"

    async with websockets.connect(ws_url) as ws:
        raw = await ws.recv()
        data = json.loads(raw)
        assert data["type"] == "assist_ready"
        assert data["session_id"] == session_id
        assert "坐席辅助服务就绪" in data.get("message", "")


async def test_websocket_ping_pong(assist_server: str):
    """WebSocket ping/pong 保活：JSON ping 和纯文本 ping 均响应"""
    session_id = "e2e-ws-ping-" + uuid_module.uuid4().hex[:8]
    ws_url = f"ws://127.0.0.1:{ASSIST_WS_PORT}/api/ws/{session_id}"

    async with websockets.connect(ws_url) as ws:
        # 等待 assist_ready
        ready = await ws.recv()
        assert json.loads(ready)["type"] == "assist_ready"

        # JSON ping
        await ws.send(json.dumps({"type": "ping"}))
        pong = await ws.recv()
        assert json.loads(pong)["type"] == "pong"

        # 纯文本 ping（兼容前端）
        await ws.send("ping")
        pong_text = await ws.recv()
        assert pong_text == "pong"


@pytest.mark.slow
async def test_websocket_customer_message(assist_server: str):
    """WebSocket 接收客户消息并推送 AssistPushMessage

    完整辅助链路：
    1. WebSocket 连接 → assist_ready
    2. 发送 customer_message → orchestrator 并行执行
       （话术匹配 + 知识检索 + 告警检查 + 产品推荐）
    3. 接收 assist_push 响应
    """
    session_id = "e2e-ws-customer-" + uuid_module.uuid4().hex[:8]
    ws_url = f"ws://127.0.0.1:{ASSIST_WS_PORT}/api/ws/{session_id}"

    async with websockets.connect(ws_url) as ws:
        # 等待就绪
        ready = await ws.recv()
        assert json.loads(ready)["type"] == "assist_ready"

        # 发送客户消息
        customer_msg = {
            "type": "customer_message",
            "message": "我想了解一下信用卡年费减免政策",
            "intent": "faq",
            "sentiment": "neutral",
            "context": "客户咨询年费问题",
            "variables": {"customer_name": "张三"},
        }
        await ws.send(json.dumps(customer_msg, ensure_ascii=False))

        # 接收推送（最多等 15s，因为可能调用 LLM）
        raw = await ws.recv()
        push_data = json.loads(raw)

        assert push_data["type"] == "assist_push", (
            f"Expected assist_push, got {push_data.get('type')}: {push_data}"
        )
        assert push_data["session_id"] == session_id

        payload = push_data.get("payload", {})
        assert "scripts" in payload
        assert "knowledge" in payload
        assert "alerts" in payload
        assert "recommendations" in payload


async def test_websocket_invalid_json(assist_server: str):
    """WebSocket 发送无效 JSON 返回 error 消息"""
    session_id = "e2e-ws-invalid-" + uuid_module.uuid4().hex[:8]
    ws_url = f"ws://127.0.0.1:{ASSIST_WS_PORT}/api/ws/{session_id}"

    async with websockets.connect(ws_url) as ws:
        ready = await ws.recv()
        assert json.loads(ready)["type"] == "assist_ready"

        await ws.send("这不是有效的JSON{{{")
        error = await ws.recv()
        error_data = json.loads(error)
        assert error_data["type"] == "error"
        assert "JSON" in error_data.get("message", "")


async def test_websocket_connection_close(assist_server: str):
    """WebSocket 连接正常关闭"""
    session_id = "e2e-ws-close-" + uuid_module.uuid4().hex[:8]
    ws_url = f"ws://127.0.0.1:{ASSIST_WS_PORT}/api/ws/{session_id}"

    async with websockets.connect(ws_url) as ws:
        ready = await ws.recv()
        assert json.loads(ready)["type"] == "assist_ready"

        await ws.close()
        assert ws.close_code is not None


# ── 并发测试 ──


@pytest.mark.slow
async def test_concurrent_chat_sessions(bot_client: httpx.AsyncClient):
    """3 个并发聊天会话独立处理，互不干扰"""
    import asyncio

    async def send_and_poll(session_id: str, message: str) -> dict:
        await bot_client.post("/api/chat/send", json={
            "message": message,
            "session_id": session_id,
        })
        await asyncio.sleep(4)
        poll_resp = await bot_client.get("/api/chat/poll", params={
            "session_id": session_id,
            "timeout": 10,
        })
        return poll_resp.json()

    sessions = [
        ("e2e-concurrent-a-" + uuid_module.uuid4().hex[:6], "你好"),
        ("e2e-concurrent-b-" + uuid_module.uuid4().hex[:6], "信用卡额度怎么查"),
        ("e2e-concurrent-c-" + uuid_module.uuid4().hex[:6], "积分怎么兑换"),
    ]

    results = await asyncio.gather(*[
        send_and_poll(sid, msg) for sid, msg in sessions
    ])

    for i, result in enumerate(results):
        assert result["has_message"] is True, f"Session {i}: {result}"
        assert len(result["reply"]) > 0
