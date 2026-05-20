"""坐席辅助服务 HTTP/WebSocket 路由"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Literal

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from smartcs.shared.exceptions import SmartCSError
from smartcs.shared.models import (
    IntentLabel,
    SentimentLabel,
    SessionPhase,
    SessionSubPhase,
    SessionUpdateRequest,
    SessionUpdateResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assist"])

# WebSocket 连接池 key，存储在 app.state 上
WS_POOL_KEY = "assist_ws_connections"


# ── 请求模型 ──


class AnalyzeRequest(BaseModel):
    """star-connection 回调：请求分析客户消息"""
    session_id: str
    message: str
    customer_id: str | None = None


# ── Health ──


@router.get("/health")
async def health_check():
    """坐席辅助服务健康检查"""
    return {"status": "healthy", "service": "assist"}


# ── Session Update ──


@router.post("/session/update")
async def session_update(body: SessionUpdateRequest, request: Request):
    """Receive session state callback from star-connection"""
    app = request.app
    session_manager = getattr(app.state, "session_manager", None)
    if session_manager is None:
        raise SmartCSError(code=5001, message="会话管理器未就绪")

    try:
        phase = SessionPhase(body.phase.lower())
    except ValueError:
        raise SmartCSError(code=2001, message=f"无效的会话阶段: {body.phase}") from None

    new_sub_phase = None
    if body.sub_phase:
        try:
            new_sub_phase = SessionSubPhase(body.sub_phase)
        except ValueError:
            raise SmartCSError(code=2001, message=f"无效的子阶段: {body.sub_phase}") from None

    reason = body.end_reason or body.agent_id or ""
    await session_manager.transition_phase(
        session_id=body.session_id,
        new_phase=phase,
        new_sub_phase=new_sub_phase,
        reason=reason,
    )
    logger.info("Session %s updated to %s:%s", body.session_id, phase.value, new_sub_phase.value if new_sub_phase else "-")
    return SessionUpdateResponse(status="ok")


# ── Analyze（star-connection 回调，生产级入口）──


@router.post("/analyze")
async def analyze_message(body: AnalyzeRequest, request: Request):
    """star-connection 回调：分析客户消息并推送辅助结果给坐席

    由 star-connection 在收到客户消息时调用。
    SmartCS 执行完整分析链路后将结果推送到对应 WebSocket。
    优先通过 Temporal OrchestrationWorkflow 编排，不可用时降级到旧 AssistOrchestrator。
    """
    app = request.app
    classifier = getattr(app.state, "classifier", None)
    ws_pool: dict[str, WebSocket] = getattr(app.state, WS_POOL_KEY, {})

    # 1. 意图分类（Rule 快路 + LLM 慢路）
    # classify() 返回 (IntentResult, entities, sentiment, source)
    intent = IntentLabel.FAQ
    confidence = 0.0
    if classifier:
        try:
            intent_result, _, _, source = await asyncio.wait_for(
                classifier.classify(body.message),
                timeout=3.0,
            )
            intent = intent_result.primary_intent
            confidence = intent_result.primary_confidence
            logger.debug("分类结果: intent=%s conf=%.2f source=%s", intent.value, confidence, source)
        except TimeoutError:
            logger.warning("意图分类超时，使用默认 FAQ")
        except Exception as e:
            logger.warning("意图分类失败: %s，使用默认 FAQ", e)

    # 2. 执行编排
    t0 = time.monotonic()
    push_data = None

    # 尝试通过 Temporal Workflow 执行
    temporal_client = getattr(app.state, "temporal_client", None)
    if temporal_client is not None:
        try:
            from smartcs.shared.config import get_settings
            from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow
            from smartcs.workflows.shared import ExecutorInput

            settings = get_settings()
            state_snapshot: dict = {}
            state_manager = getattr(app.state, "state_manager", None)
            if state_manager:
                snapshot = await state_manager.read_state(body.session_id)
                state_snapshot = snapshot or {"last_confidence": confidence}

            workflow_input = ExecutorInput(
                session_id=body.session_id,
                message=body.message,
                intent=intent.value,
                sentiment="neutral",
                state_snapshot=state_snapshot,
                trace_id=body.session_id,  # simplified
            )

            result = await asyncio.wait_for(
                temporal_client.execute_workflow(
                    OrchestrationWorkflow.run,
                    workflow_input,
                    id=f"assist-{body.session_id}-{int(time.monotonic()*1000)}",
                    task_queue=settings.temporal.task_queue,
                ),
                timeout=settings.orchestration.global_timeout_ms / 1000,
            )
            push_data = {
                "type": "assist_push",
                "session_id": body.session_id,
                "trigger": "customer_message",
                "payload": {
                    "primary_card": result.primary_card,
                    "risk_badge": result.risk_badge,
                    "marketing_slot": result.marketing_slot,
                    "fusion_type": result.fusion_type,
                    "trace_id": result.trace_id,
                },
            }
        except TimeoutError:
            logger.warning("Temporal Workflow 超时 session=%s", body.session_id)
        except Exception as e:
            logger.warning("Temporal Workflow 执行失败: %s，降级到同步编排", e)

    # 降级: 使用旧编排器
    if push_data is None:
        orchestrator = getattr(app.state, "assist_orchestrator", None)
        if orchestrator:
            try:
                push_msg = await asyncio.wait_for(
                    orchestrator.process(
                        session_id=body.session_id,
                        message=body.message,
                        intent=intent,
                        sentiment=SentimentLabel.NEUTRAL,
                        sentiment_history=[],
                        context=body.message,
                    ),
                    timeout=5.0,
                )
                push_data = push_msg.model_dump(mode="json")
            except TimeoutError:
                push_data = {
                    "type": "assist_push",
                    "session_id": body.session_id,
                    "trigger": "customer_message",
                    "payload": {},
                }
            except Exception:
                push_data = {
                    "type": "assist_push",
                    "session_id": body.session_id,
                    "trigger": "customer_message",
                    "payload": {},
                }
        else:
            push_data = {
                "type": "assist_push",
                "session_id": body.session_id,
                "trigger": "customer_message",
                "payload": {},
            }

    elapsed = (time.monotonic() - t0) * 1000
    logger.info(
        "analyze session=%s intent=%s confidence=%.2f elapsed=%.1fms",
        body.session_id, intent.value, confidence, elapsed,
    )

    # 3. WebSocket 推送
    ws = ws_pool.get(body.session_id)
    if ws is not None:
        try:
            await ws.send_json(push_data)
            logger.debug(
                "推送成功 session=%s",
                body.session_id,
            )
        except Exception:
            logger.warning("WebSocket 推送失败 session=%s，移除连接", body.session_id)
            ws_pool.pop(body.session_id, None)
    else:
        logger.warning(
            "session=%s 无坐席连接，跳过推送 (pool_size=%d)",
            body.session_id, len(ws_pool),
        )

    return {"status": "ok", "intent": intent.value, "confidence": confidence}


# ── Feedback（隐式反馈，对应设计文档 §3.6）──


class FeedbackRequest(BaseModel):
    """反馈请求"""

    session_id: str
    agent_id: str
    action: Literal["accept", "modify", "partial_accept", "reject"] = "reject"
    modify_fields: list[str] = Field(default_factory=list)


def _action_to_confidence(action: str) -> float:
    """操作类型 → 置信度映射（对应文档 §3.6）"""
    mapping = {
        "accept": 1.0,
        "modify": 0.5,
        "partial_accept": 0.3,
        "reject": 0.0,
    }
    return mapping.get(action, 0.0)


# H2: 反馈延迟确认缓冲区（3 秒内可撤销）
_feedback_buffer: dict[str, dict] = {}  # session_id → feedback_data
_feedback_tasks: set[asyncio.Task] = set()  # 保持任务引用，防止 GC 回收


@router.post("/feedback")
async def record_feedback(body: FeedbackRequest, request: Request):
    """记录隐式反馈信号

    对应设计文档 §3.6 反馈闭环层:
    - 直接发送 → accept, confidence 1.0
    - 修改后发送 → modify, confidence 0.5, modify_fields
    - 复制部分内容 → partial_accept, confidence 0.3
    - 忽略 → reject, confidence 0.0

    H2: 3 秒延迟确认 — 缓冲反馈写入，允许 3 秒内撤销。
    """
    app = request.app
    confidence = _action_to_confidence(body.action)

    # H2: 缓冲反馈，3 秒后才提交到 Redis
    buffer_key = f"{body.session_id}:{body.agent_id}"
    feedback_data = {
        "action": body.action,
        "confidence": confidence,
        "modify_fields": body.modify_fields,
        "agent_id": body.agent_id,
    }
    _feedback_buffer[buffer_key] = feedback_data

    # 启动延迟提交任务
    feedback_task = asyncio.create_task(_commit_feedback_after_delay(
        app, body.session_id, buffer_key, feedback_data, delay=3.0,
    ))
    _feedback_tasks.add(feedback_task)
    feedback_task.add_done_callback(_feedback_tasks.discard)

    logger.info(
        "反馈(缓冲) session=%s agent=%s action=%s confidence=%.1f",
        body.session_id, body.agent_id, body.action, confidence,
    )

    return {"status": "ok", "action": body.action, "confidence": confidence, "delayed_commit": True}


@router.post("/feedback/undo")
async def undo_feedback(body: FeedbackRequest, request: Request):
    """H2: 撤销缓冲中的反馈

    在 3 秒延迟期内，坐席可以撤销反馈。
    """
    buffer_key = f"{body.session_id}:{body.agent_id}"
    if buffer_key in _feedback_buffer:
        del _feedback_buffer[buffer_key]
        logger.info("反馈撤销: session=%s agent=%s", body.session_id, body.agent_id)
        return {"status": "ok", "undone": True}
    return {"status": "ok", "undone": False, "reason": "not_buffered"}


async def _commit_feedback_after_delay(
    app: object,
    session_id: str,
    buffer_key: str,
    feedback_data: dict,
    delay: float = 3.0,
) -> None:
    """H2: 延迟提交反馈到 Redis"""
    await asyncio.sleep(delay)

    # 检查是否已被撤销
    if buffer_key not in _feedback_buffer:
        return

    # 从缓冲区移除
    _feedback_buffer.pop(buffer_key, None)

    # 写入反馈到 Redis 状态
    state_manager = getattr(app, "state", None)  # type: ignore[attr-defined]
    if state_manager is None:
        return
    state_manager = getattr(state_manager, "state_manager", None)
    if state_manager is None:
        return

    try:
        state = await state_manager.read_state(session_id)  # type: ignore[attr-defined]
        if state:
            await state_manager.cas_write(  # type: ignore[attr-defined]
                session_id=session_id,
                expected_version=state.get("version", 1),
                patches={
                    "last_feedback": feedback_data,
                },
                writer=f"feedback:{feedback_data.get('agent_id', '')}",
            )
    except Exception as e:
        logger.warning("反馈提交失败: session=%s error=%s", session_id, e)


# ── WebSocket ──


@router.websocket("/ws/{session_id}")
async def assist_websocket(websocket: WebSocket, session_id: str):
    """坐席辅助 WebSocket

    生命周期：
    1. 接受连接 → 注册到连接池 → 发送就绪消息
    2. 心跳（每 15s ping）
    3. 接收客户消息（前端直接发送，用于手动触发分析）
    4. 断连 → 从连接池移除
    """
    await websocket.accept()

    app = websocket.app
    orchestrator = getattr(app.state, "assist_orchestrator", None)
    session_manager = getattr(app.state, "session_manager", None)

    if orchestrator is None or session_manager is None:
        await websocket.send_json({"type": "error", "message": "服务未就绪"})
        await websocket.close()
        return

    # 注册到连接池（供 analyze 端点推送）
    ws_pool: dict[str, WebSocket] = getattr(app.state, WS_POOL_KEY, {})
    ws_pool[session_id] = websocket
    logger.debug("WebSocket 注册: session=%s, pool_size=%d", session_id, len(ws_pool))

    # 加载会话历史
    sentiment_history = await _load_sentiment_history(session_manager, session_id)

    # 发送就绪消息
    await websocket.send_json({
        "type": "assist_ready",
        "session_id": session_id,
        "message": "坐席辅助服务就绪",
    })

    # 心跳任务
    heartbeat_task = asyncio.create_task(_heartbeat(websocket))

    try:
        await _handle_messages(websocket, orchestrator, session_id, sentiment_history)
    except TimeoutError:
        logger.info("WebSocket session=%s 超时关闭", session_id)
    except WebSocketDisconnect:
        logger.info("WebSocket session=%s 客户端断开", session_id)
    finally:
        # 从连接池移除
        ws_pool.pop(session_id, None)
        logger.debug("WebSocket 注销: session=%s, pool_size=%d", session_id, len(ws_pool))

        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


async def _load_sentiment_history(session_manager: object, session_id: str) -> list[SentimentLabel]:
    """从会话管理器加载历史情绪标签"""
    sentiment_history: list[SentimentLabel] = []
    try:
        session_state = await session_manager.get_session(session_id)  # type: ignore[attr-defined]
        if session_state:
            for turn in session_state.turns:
                if turn.emotion_label and turn.speaker == "customer":
                    sentiment_history.append(turn.emotion_label)
    except Exception as e:
        logger.warning("加载会话 %s 失败: %s", session_id, e)
    return sentiment_history


async def _handle_messages(
    websocket: WebSocket,
    orchestrator: object,
    session_id: str,
    sentiment_history: list[SentimentLabel],
) -> None:
    """处理 WebSocket 消息循环"""
    while True:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

        if raw == "ping":
            await websocket.send_text("pong")
            continue

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
            await _process_customer_message(websocket, orchestrator, session_id, data, sentiment_history)


async def _process_customer_message(
    websocket: WebSocket,
    orchestrator: object,
    session_id: str,
    data: dict,
    sentiment_history: list[SentimentLabel],
) -> None:
    """处理客户消息并推送结果"""
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
    push_msg = await orchestrator.process(  # type: ignore[attr-defined]
        session_id=session_id,
        message=message,
        intent=intent,
        sentiment=sentiment,
        sentiment_history=sentiment_history,
        context=context,
        variables=variables,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000

    sentiment_history.append(sentiment)
    if len(sentiment_history) > 20:
        del sentiment_history[:-20]

    has_critical = any(
        a.level == "critical"
        for a in push_msg.payload.alerts
    )
    if has_critical or not orchestrator.should_throttle(session_id):  # type: ignore[attr-defined]
        await websocket.send_json(push_msg.model_dump(mode="json"))
        logger.debug("推送至 session=%s, elapsed=%.1fms", session_id, elapsed_ms)
    else:
        logger.debug("节流跳过 session=%s", session_id)


async def _heartbeat(websocket: WebSocket, interval: float = 15.0):
    """心跳发送"""
    while True:
        await asyncio.sleep(interval)
        try:
            await websocket.send_json({"type": "heartbeat"})
        except Exception:
            break
