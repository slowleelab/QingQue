"""WebSocket 路由单元测试 (ws_router.py, mock WebSocket)"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from lumio.services.bot import ws_router
from lumio.services.bot.ws_router import _process_streaming


class _FakeWS:
    """模拟 WebSocket 客户端"""

    def __init__(self, messages: list[str], query_params: dict | None = None):
        self._messages = list(messages)
        self.sent: list[dict] = []
        self.closed_with: tuple[int, str] | None = None
        self.accepted = False
        self.query_params = query_params if query_params is not None else {"token": "valid-token"}
        self.app = MagicMock()

    async def receive_text(self) -> str:
        if not self._messages:
            await asyncio.sleep(3600)  # 无消息时挂起 (测试通过取消终止)
        return self._messages.pop(0)

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


@pytest.fixture
def valid_token() -> str:
    from lumio.shared.auth import create_access_token

    return create_access_token("ws-user-1", "customer")


# ── 认证 ──


async def test_ws_missing_token():
    """无 token → 4401 关闭"""
    ws = _FakeWS([], query_params={})
    await ws_router.chat_websocket(ws, "s1")
    assert ws.closed_with == (4401, "缺少认证 token")
    assert not ws.accepted


async def test_ws_invalid_token():
    """token 无效 → 4401 关闭"""
    ws = _FakeWS([], query_params={"token": "garbage-token"})
    await ws_router.chat_websocket(ws, "s1")
    assert ws.closed_with == (4401, "token 无效")


async def test_ws_valid_token_invalid_json(valid_token: str):
    """合法 token + 坏 JSON → error 事件后继续"""
    ws = _FakeWS(["not-json{{{"], query_params={"token": valid_token})
    task = asyncio.create_task(ws_router.chat_websocket(ws, "s1"))
    await asyncio.sleep(0.1)
    assert ws.accepted is True
    assert any(m.get("type") == "error" for m in ws.sent)
    task.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await task


async def test_ws_cancel_message(valid_token: str):
    """cancel 消息 → cancelled 事件"""
    ws = _FakeWS(['{"type": "cancel"}'], query_params={"token": valid_token})
    task = asyncio.create_task(ws_router.chat_websocket(ws, "s1"))
    await asyncio.sleep(0.1)
    assert any(m.get("type") == "cancelled" for m in ws.sent)
    task.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await task


# ── _process_streaming ──


async def test_process_streaming_yields_deltas(monkeypatch):
    """流式事件: delta + done"""
    messages = []

    class _FakeClient:
        async def stream_chat(self, msgs, cancel_event=None):
            messages.append(msgs)
            yield "你好"
            yield "世界"

    monkeypatch.setattr("lumio.services.bot.streaming.get_streaming_client", lambda: _FakeClient())
    cancel = asyncio.Event()

    events = [e async for e in _process_streaming("s1", "年费", cancel)]
    types = [e["type"] for e in events]
    assert types == ["delta", "delta", "done"]
    assert events[-1]["full_text"] == "你好世界"
    # 消息组装: system + user
    assert messages[0][0]["role"] == "system"
    assert messages[0][-1] == {"role": "user", "content": "年费"}


async def test_process_streaming_with_history(monkeypatch):
    """历史加载 → 交替消息"""
    history = []

    class _Turn:
        def __init__(self, speaker, content):
            self.speaker = speaker
            self.content = content

    history = [_Turn("customer", "之前的问题"), _Turn("bot", "之前的回答")]

    class _SM:
        async def get_history(self, session_id, limit=10):
            return history

    class _FakeClient:
        async def stream_chat(self, msgs, cancel_event=None):
            yield "ok"

    monkeypatch.setattr("lumio.services.bot.streaming.get_streaming_client", lambda: _FakeClient())
    events = [e async for e in _process_streaming("s1", "现在的问题", asyncio.Event(), _SM())]
    assert events[-1]["type"] == "done"


async def test_process_streaming_history_error(monkeypatch):
    """历史加载失败 → 不阻断流式"""

    class _BoomSM:
        async def get_history(self, session_id, limit=10):
            raise RuntimeError("redis down")

    class _FakeClient:
        async def stream_chat(self, msgs, cancel_event=None):
            yield "仍然回复"

    monkeypatch.setattr("lumio.services.bot.streaming.get_streaming_client", lambda: _FakeClient())
    events = [e async for e in _process_streaming("s1", "hi", asyncio.Event(), _BoomSM())]
    assert events[-1]["type"] == "done"
    assert events[-1]["full_text"] == "仍然回复"


async def test_process_streaming_cancelled(monkeypatch):
    """流式被取消 → cancelled 事件"""

    class _FakeClient:
        async def stream_chat(self, msgs, cancel_event=None):
            yield "a"
            raise asyncio.CancelledError()

    monkeypatch.setattr("lumio.services.bot.streaming.get_streaming_client", lambda: _FakeClient())
    events = [e async for e in _process_streaming("s1", "hi", asyncio.Event())]
    assert events[-1]["type"] == "cancelled"
