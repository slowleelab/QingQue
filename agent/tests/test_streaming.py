"""流式 LLM 客户端单元测试 (streaming.py)"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lumio.services.bot.streaming import StreamingLLMClient


def _chunk(content: str | None) -> MagicMock:
    c = MagicMock()
    c.choices = [MagicMock(delta=MagicMock(content=content))]
    return c


async def _collect(agen) -> list[str]:
    return [x async for x in agen]


async def test_stream_chat_yields_deltas():
    """正常流式输出"""
    client = StreamingLLMClient()
    chunks = [_chunk("你好"), _chunk("世界")]
    response = AsyncMock()
    response.__aiter__ = lambda self: _aiter(chunks)

    with patch.object(client, "_client") as mock:
        mock.chat.completions.create = AsyncMock(return_value=response)
        out = await _collect(client.stream_chat([{"role": "user", "content": "hi"}]))
    assert out == ["你好", "世界"]
    # stream=True 传入
    assert mock.chat.completions.create.call_args.kwargs["stream"] is True


async def _aiter(items):
    for i in items:
        yield i


async def test_stream_chat_skips_empty_and_no_choices():
    """空 delta 与无 choices 的 chunk 跳过"""
    client = StreamingLLMClient()
    chunks = [_chunk(None), _chunk(""), _chunk("有效")]
    response = AsyncMock()
    response.__aiter__ = lambda self: _aiter(chunks)
    with patch.object(client, "_client") as mock:
        mock.chat.completions.create = AsyncMock(return_value=response)
        out = await _collect(client.stream_chat([{"role": "user", "content": "hi"}]))
    assert out == ["有效"]


async def test_stream_chat_cancel_event():
    """cancel_event 设置后提前终止"""
    client = StreamingLLMClient()
    cancel = asyncio.Event()
    chunks = [_chunk("a"), _chunk("b")]
    response = AsyncMock()
    response.__aiter__ = lambda self: _aiter(chunks)
    with patch.object(client, "_client") as mock:
        mock.chat.completions.create = AsyncMock(return_value=response)
        cancel.set()  # 一开始就取消
        out = await _collect(client.stream_chat([{"role": "user", "content": "hi"}], cancel_event=cancel))
    assert out == []


async def test_stream_chat_exception_raises():
    """流式异常向上传播"""
    client = StreamingLLMClient()
    with patch.object(client, "_client") as mock:
        mock.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await _collect(client.stream_chat([{"role": "user", "content": "hi"}]))


async def test_format_sse_events():
    """SSE 格式: delta 事件 + done 标记"""
    client = StreamingLLMClient()
    chunks = [_chunk("你好")]
    response = AsyncMock()
    response.__aiter__ = lambda self: _aiter(chunks)
    with patch.object(client, "_client") as mock:
        mock.chat.completions.create = AsyncMock(return_value=response)
        out = await _collect(client.format_sse([{"role": "user", "content": "hi"}]))
    assert len(out) == 2
    assert out[0].startswith("data: ")
    assert "你好" in out[0]
    assert out[1] == 'data: {"type":"done"}\n\n'


async def test_format_sse_error_event():
    """SSE 内部异常 → 返回 error 事件 (含 trace_id, 不暴露内部异常)"""
    client = StreamingLLMClient()
    with patch.object(client, "_client") as mock:
        mock.chat.completions.create = AsyncMock(side_effect=RuntimeError("secret path /var/x"))
        out = await _collect(client.format_sse([{"role": "user", "content": "hi"}]))
    assert len(out) == 1
    assert "INTERNAL_ERROR" in out[0]
    assert "trace_id" in out[0]
    assert "/var/x" not in out[0]  # 不暴露内部信息


def test_get_streaming_client_singleton():
    """单例"""
    from lumio.services.bot.streaming import get_streaming_client

    assert get_streaming_client() is get_streaming_client()
