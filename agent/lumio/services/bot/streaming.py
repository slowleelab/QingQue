"""F0: 流式 LLM 客户端 + 客户端中断.

3 大能力:
1. async for chunk 流式输出
2. SSE 格式 (text/event-stream)
3. 客户端中断 (asyncio.Task.cancel + request.is_disconnected)

性能:
- TTFT 单独打点 (首 token 延迟)
- chunk 数统计
- 取消原因分类 (client_disconnect / user_cancel / timeout)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from lumio.shared.config import get_settings
from lumio.shared.logger import get_logger
from lumio.shared.metrics import (
    LLM_STREAM_CANCELLED,
    LLM_STREAM_CHUNKS,
    LLM_STREAM_TTFT,
)

logger = get_logger(__name__)


class StreamingLLMClient:
    """流式 LLM 客户端 (替代阻塞式 chat).

    与 LLMClient 共用熔断器, 但走流式路径.
    """

    def __init__(self) -> None:
        settings = get_settings().llm
        self._client = AsyncOpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=settings.timeout_seconds,
        )
        self._model = settings.primary_model

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        """流式 LLM 调用 (async generator).

        Args:
            messages: messages 数组
            cancel_event: 客户端取消事件, 设置后停止生成
            model/temperature/max_tokens: LLM 参数

        Yields:
            每次生成的 chunk 字符串
        """
        settings = get_settings().llm
        _start = time.monotonic()
        first_token = True
        chunk_count = 0

        try:
            response = await self._client.chat.completions.create(
                model=model or self._model,
                messages=messages,
                temperature=temperature if temperature is not None else settings.temperature,
                max_tokens=max_tokens or settings.max_tokens,
                stream=True,
            )

            async for chunk in response:
                if cancel_event and cancel_event.is_set():
                    LLM_STREAM_CANCELLED.labels(reason="client_disconnect").inc()
                    logger.debug("流式响应被客户端取消")
                    return
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if not delta:
                    continue
                if first_token:
                    ttft = time.monotonic() - _start
                    LLM_STREAM_TTFT.labels(model=model or self._model).observe(ttft)
                    first_token = False
                    logger.debug("流式首 token: %.3fs", ttft)
                chunk_count += 1
                LLM_STREAM_CHUNKS.labels(model=model or self._model).inc()
                yield delta
        except asyncio.CancelledError:
            LLM_STREAM_CANCELLED.labels(reason="user_cancel").inc()
            logger.debug("流式响应被 asyncio 取消")
            raise
        except Exception as exc:
            logger.error("流式 LLM 调用失败: %s", exc)
            LLM_STREAM_CANCELLED.labels(reason="error").inc()
            raise

    async def format_sse(
        self,
        messages: list[dict[str, Any]],
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        """格式化为 SSE 事件流.

        Yields:
            "data: {json}\\n\\n" 格式字符串, FastAPI StreamingResponse 直接消费
        """
        import json

        try:
            async for chunk in self.stream_chat(messages, cancel_event=cancel_event):
                event = json.dumps({"type": "delta", "content": chunk}, ensure_ascii=False)
                yield f"data: {event}\n\n"
            # 结束标记
            yield 'data: {"type":"done"}\n\n'
        except asyncio.CancelledError:
            # 客户端取消: 真中断, raise 让 FastAPI 终止 generator
            # 不再 yield 假 "cancelled" 事件 (会先 flush 给客户端, 浪费 token)
            raise
        except Exception as exc:
            # S8 第五轮修复: 不暴露内部异常 (SQL/文件路径/库版本) — 返回 trace_id
            import uuid as _uuid

            trace_id = _uuid.uuid4().hex[:12]
            logger.error("SSE 流式失败: trace=%s err=%s", trace_id, exc)
            error_event = json.dumps(
                {
                    "type": "error",
                    "code": "INTERNAL_ERROR",
                    "trace_id": trace_id,
                    "message": "服务暂时不可用, 请稍后重试",
                },
                ensure_ascii=False,
            )
            yield f"data: {error_event}\n\n"


# 全局单例
_streaming_client: StreamingLLMClient | None = None


def get_streaming_client() -> StreamingLLMClient:
    global _streaming_client
    if _streaming_client is None:
        _streaming_client = StreamingLLMClient()
    return _streaming_client
