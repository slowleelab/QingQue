"""Sprint D: 客户侧 WebSocket 路由 (B1 部分).

提供 WS /api/chat/ws/{session_id} 端点, 客户端发 message → 服务端流式 push
(复用 F0 流式 + A2UI 卡片).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from lumio.shared.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/api/chat/ws/{session_id}")
async def chat_websocket(websocket: WebSocket, session_id: str) -> None:
    """客户侧 WebSocket 端点 (B1: 替代长轮询).

    协议:
    1. 客户端 connect (query param 带 token) → 服务端鉴权后接受
    2. 客户端 send {"type": "message", "content": "..."} → 服务端流式 push
    3. 服务端 push:
       - {"type": "thinking", "content": "正在查询..."}  (LLM 调用前)
       - {"type": "delta", "content": "..."}            (每个 token)
       - {"type": "tool_call", "tool": "...", "args": ...}  (工具调用时)
       - {"type": "tool_result", "tool": "...", "result": ...} (工具返回时)
       - {"type": "card", "card": {...}}                (A2UI 卡片)
       - {"type": "quick_reply", "options": [...]}       (快速回复)
       - {"type": "done", "full_text": "..."}            (完成)
    4. 客户端可发 {"type": "cancel"} 中断 (流式期间实时生效)
    """
    # S8 第五轮修复: WS 鉴权 — 旧实现 accept() 后无任何 token 校验, 挂载即匿名直通
    from lumio.shared.auth import decode_token

    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4401, reason="缺少认证 token")
        return
    try:
        payload = decode_token(token)
    except Exception:
        await websocket.close(code=4401, reason="token 无效")
        return
    ws_user_id = payload.get("sub", "")
    logger.info("WS 连接: session=%s user=%s", session_id, ws_user_id)

    await websocket.accept()
    cancel_event = asyncio.Event()

    try:
        while True:
            # 接收客户端消息
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "message": "invalid_json"}
                )
                continue

            msg_type = msg.get("type")

            # ── 取消 ──
            if msg_type == "cancel":
                # 同一 event 反复用 — clear() 后 stream_chat 仍读到同一对象
                # 重赋值会导致 stream_chat 内部闭包仍引用旧 event (race)
                cancel_event.set()
                await websocket.send_json({"type": "cancelled"})
                cancel_event.clear()  # 重置, 保持同一对象引用
                continue

            # ── ping/pong (心跳) ──
            if msg_type == "ping":
                await websocket.send_json({"type": "pong", "ts": time.time()})
                continue

            # ── 业务消息 ──
            if msg_type == "message":
                user_input = msg.get("content", "").strip()
                if not user_input:
                    await websocket.send_json(
                        {"type": "error", "message": "empty_content"}
                    )
                    continue

                # S8 第五轮修复: 并发取消 — 旧实现 receive 循环与流式串行,
                # 生成期间阻塞在 async for, 根本收不到 cancel 消息.
                # 现: 流式跑独立 task, 主循环持续 receive, cancel 实时生效.
                cancel_event.clear()

                # 思考提示
                await websocket.send_json(
                    {"type": "thinking", "content": "正在思考..."}
                )

                # 默认参数绑定当前 user_input, 防 B023 循环变量捕获
                async def _run_stream(_ui: str = user_input) -> None:
                    async for chunk_event in _process_streaming(
                        session_id, _ui, cancel_event
                    ):
                        await websocket.send_json(chunk_event)

                stream_task = asyncio.create_task(_run_stream())
                try:
                    while not stream_task.done():
                        try:
                            ctrl = await asyncio.wait_for(
                                websocket.receive_text(), timeout=0.1
                            )
                        except TimeoutError:
                            continue
                        try:
                            ctrl_msg = json.loads(ctrl)
                        except json.JSONDecodeError:
                            continue
                        if ctrl_msg.get("type") == "cancel":
                            cancel_event.set()
                            stream_task.cancel()
                            await websocket.send_json({"type": "cancelled"})
                            break
                        if ctrl_msg.get("type") == "ping":
                            await websocket.send_json({"type": "pong", "ts": time.time()})
                finally:
                    cancel_event.clear()
                # 收集流式结果异常
                try:
                    await stream_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    # 不直接暴露内部错误信息 — 返回 trace_id
                    import uuid as _uuid

                    trace_id = _uuid.uuid4().hex[:12]
                    logger.error(
                        "WS 处理失败: session=%s trace=%s err=%s",
                        session_id,
                        trace_id,
                        exc,
                    )
                    with contextlib.suppress(Exception):
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": "INTERNAL_ERROR",
                                "trace_id": trace_id,
                                "message": "服务暂时不可用, 请稍后重试",
                            }
                        )

    except WebSocketDisconnect:
        logger.info("WS 断开: session=%s", session_id)
    except Exception as exc:
        logger.error("WS 异常: session=%s err=%s", session_id, exc)
        with contextlib.suppress(Exception):
            await websocket.close()


async def _process_streaming(
    session_id: str, user_input: str, cancel_event: asyncio.Event
):
    """处理单条消息并 yield 流式事件 (Yields 字典)."""
    # 简化实现: 实际接入 Bot Agent + StreamingLLMClient
    # 1. 加载历史
    # 2. 拼 messages (用 A0 分层构建器)
    # 3. 调流式 LLM
    # 4. 收集 chunk, 周期性 yield
    # 简化版 (无完整 Bot Agent 集成)
    from lumio.services.bot.prompts import KNOWLEDGE_SYSTEM_PROMPT
    from lumio.services.bot.streaming import get_streaming_client

    messages = [
        {"role": "system", "content": KNOWLEDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]

    client = get_streaming_client()
    full_text = ""
    try:
        async for chunk in client.stream_chat(messages, cancel_event=cancel_event):
            full_text += chunk
            yield {"type": "delta", "content": chunk}
    except asyncio.CancelledError:
        yield {"type": "cancelled"}
        return

    yield {"type": "done", "full_text": full_text}
