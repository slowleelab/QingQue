"""坐席辅助服务 HTTP/WebSocket 路由"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from smartcs.shared.models import (
    AssistPushMessage,
    IntentLabel,
    SentimentLabel,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assist"])


@router.get("/health")
async def health_check():
    """坐席辅助服务健康检查"""
    return {"status": "healthy", "service": "assist"}


@router.websocket("/ws/{session_id}")
async def assist_websocket(websocket: WebSocket, session_id: str):
    """坐席辅助 WebSocket

    生命周期：
    1. 接受连接 → 发送就绪消息
    2. 心跳（每 15s ping）
    3. 接收消息 → 编排处理 → 推送结果
    4. 断连清理
    """
    await websocket.accept()

    # 获取依赖
    app = websocket.app
    orchestrator = app.state.assist_orchestrator
    session_manager = app.state.session_manager

    # 加载会话历史
    sentiment_history: list[SentimentLabel] = []
    try:
        session_state = await session_manager.get_session(session_id)
        if session_state:
            for turn in session_state.turns:
                if turn.emotion_label and turn.speaker == "customer":
                    sentiment_history.append(turn.emotion_label)
    except Exception as e:
        logger.warning("加载会话 %s 失败: %s", session_id, e)

    # 发送就绪消息
    await websocket.send_json({
        "type": "assist_ready",
        "session_id": session_id,
        "message": "坐席辅助服务就绪",
    })

    # 心跳任务
    heartbeat_task = asyncio.create_task(_heartbeat(websocket))

    try:
        while True:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "无效的 JSON"})
                continue

            msg_type = data.get("type", "customer_message")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "customer_message":
                message = data.get("message", "")
                intent_str = data.get("intent", "faq")
                sentiment_str = data.get("sentiment", "neutral")
                context = data.get("context", message)

                try:
                    intent = IntentLabel(intent_str)
                    sentiment = SentimentLabel(sentiment_str)
                except ValueError:
                    intent = IntentLabel.FAQ
                    sentiment = SentimentLabel.NEUTRAL

                variables = data.get("variables", {})

                t0 = time.monotonic()
                push_msg = await orchestrator.process(
                    session_id=session_id,
                    message=message,
                    intent=intent,
                    sentiment=sentiment,
                    sentiment_history=sentiment_history,
                    context=context,
                    variables=variables,
                )
                elapsed_ms = (time.monotonic() - t0) * 1000

                # 更新 sentiment_history
                sentiment_history.append(sentiment)
                if len(sentiment_history) > 20:
                    sentiment_history = sentiment_history[-20:]

                # 告警不受节流限制
                has_critical = any(
                    a.get("level") == "critical" if isinstance(a, dict) else getattr(a, "level", None) == "critical"
                    for a in push_msg.payload.alerts
                )
                if has_critical or not orchestrator.should_throttle(session_id):
                    await websocket.send_json(push_msg.model_dump(mode="json"))
                    logger.debug("推送至 session=%s, elapsed=%.1fms", session_id, elapsed_ms)
                else:
                    logger.debug("节流跳过 session=%s", session_id)

    except asyncio.TimeoutError:
        logger.info("WebSocket session=%s 超时关闭", session_id)
    except WebSocketDisconnect:
        logger.info("WebSocket session=%s 客户端断开", session_id)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _heartbeat(websocket: WebSocket, interval: float = 15.0):
    """心跳发送"""
    while True:
        await asyncio.sleep(interval)
        try:
            await websocket.send_json({"type": "heartbeat"})
        except Exception:
            break
