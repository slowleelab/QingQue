"""坐席辅助服务 HTTP/WebSocket 路由"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Literal

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from smartcs.services.assist.summary import generate_call_summary
from smartcs.shared.exceptions import InvalidTransitionError, SessionNotFoundError, SmartCSError
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

# WebSocket 连接池: agent_id → WebSocket (per-agent, 持久连接)
WS_POOL_KEY = "assist_ws_pool"

# ── per-session Queue + Worker (notify 消费) ─────────────────────────
# 已迁移至 Redis Pub/Sub: POST /notify → PUBLISH channel → WS handler 订阅
# 保留 _feedback_buffer / _silence 等轻量 per-connection 状态


# ── 请求模型 ──


class AnalyzeRequest(BaseModel):
    """star-connection 回调：请求分析客户消息"""

    session_id: str
    message: str
    customer_id: str | None = None


# ── Health ──


@router.get("/health")
async def health_check(request: Request):
    """坐席辅助服务健康检查（含依赖状态）"""
    from fastapi.responses import JSONResponse

    from smartcs.shared.health import aggregate_health, check_all_dependencies

    deps = await check_all_dependencies(request.app)
    overall, http_code = aggregate_health(deps)
    return JSONResponse(
        status_code=http_code,
        content={"status": overall, "service": "assist", "dependencies": deps},
    )


@router.get("/health/live")
async def health_live():
    """Liveness 探针：进程存活即 200"""
    return {"status": "alive"}


@router.get("/health/ready")
async def health_ready(request: Request):
    """Readiness 探针：检查核心依赖连通性"""
    from fastapi.responses import JSONResponse

    from smartcs.shared.health import aggregate_health, check_all_dependencies

    deps = await check_all_dependencies(request.app)
    overall, http_code = aggregate_health(deps)
    return JSONResponse(
        status_code=http_code,
        content={"status": overall, "dependencies": deps},
    )


# ── Notify (星 connect → Redis Pub/Sub, 202 → WS handler 异步处理) ──


class NotifyRequest(BaseModel):
    """star-conn 异步通知请求"""

    session_id: str
    message: str = ""
    event: str = "customer_msg"


@router.post("/notify")
async def notify_message(body: NotifyRequest, request: Request):
    """star-conn 异步通知：客户消息到达

    202 立即返回。消息通过 Redis Pub/Sub 发布到 session 专属频道，
    持有该 session WebSocket 连接的实例订阅频道并异步处理。
    """
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        raise SmartCSError(code=5001, message="Redis 未就绪")

    channel = f"smartcs:assist:notify:{body.session_id}"
    payload = json.dumps(
        {
            "session_id": body.session_id,
            "message": body.message,
            "event": body.event,
            "_enqueue_time": asyncio.get_event_loop().time(),
        },
        ensure_ascii=False,
    )
    await redis_client.publish(channel, payload)
    return JSONResponse(content={"status": "accepted"}, status_code=202)


async def _process_notify_message(app, session_id: str, websocket: WebSocket, item: dict) -> None:
    """处理单条 notify 消息: 分类 → OE 编排 → WS 推送

    从 Redis Pub/Sub 收到消息后调用，替代旧的 _notify_session_worker 循环。
    串行性由 Pub/Sub 订阅的 async for 循环自然保证。
    """
    from smartcs.shared.config import get_settings

    settings = get_settings()
    message_ttl = settings.bot.message_ttl_seconds

    message = item.get("message", "")
    enqueue_time = item.get("_enqueue_time", 0.0)

    # 1. 跳过过期消息
    if enqueue_time and asyncio.get_event_loop().time() - enqueue_time > message_ttl:
        logger.debug("notify 消息过期跳过: session=%s", session_id)
        return

    # 2. 加载会话状态
    session_manager = getattr(app.state, "session_manager", None)
    if session_manager:
        state = await session_manager.get_session(session_id)
        if state and state.current_phase != SessionPhase.AGENT:
            logger.debug("会话非 AGENT 阶段, 跳过: session=%s", session_id)
            return

    # 3. 意图分类
    classifier = getattr(app.state, "classifier", None)
    intent = IntentLabel.FAQ
    confidence = 0.0
    sentiment = SentimentLabel.NEUTRAL
    if classifier and message:
        try:
            intent_result, _entities, sentiment, _source = await asyncio.wait_for(
                classifier.classify(message),
                timeout=3.0,
            )
            intent = intent_result.primary_intent
            confidence = intent_result.primary_confidence
        except (TimeoutError, Exception):
            logger.debug("notify 分类失败: session=%s", session_id)

    # 4. OE 编排 + WS 推送
    push_data = await _run_oe_pipeline(app, session_id, message, intent, confidence, sentiment)
    if push_data:
        with contextlib.suppress(Exception):
            await websocket.send_json(push_data)


async def _run_oe_pipeline(app, session_id: str, message: str, intent, confidence, sentiment=None) -> dict | None:
    """执行 OE 编排管道, 返回 assist_push payload 或 None

    优先级: PydanticAI OE Pipeline > 同步编排器
    """
    try:
        t0 = time.monotonic()
        push_data = None

        # 优先: PydanticAI OE Pipeline
        ai_executor = getattr(app.state, "ai_executor", None)
        if ai_executor is not None:
            try:
                from smartcs.services.common.oe_pipeline import load_push_tracker, run_oe_pipeline

                redis_client = getattr(app.state, "redis_client", None)
                alert_engine = getattr(app.state, "alert_engine", None)
                degrader = getattr(app.state, "degradation_mgr", None)
                breakers = getattr(app.state, "oe_breakers", None)
                session_manager = getattr(app.state, "session_manager", None)

                state_snapshot: dict = {}
                if session_manager:
                    snapshot = await session_manager.read_state(session_id)
                    state_snapshot = snapshot or {"last_confidence": confidence}

                push_tracker = await load_push_tracker(session_id, redis_client)
                trace_id = f"{session_id}-{int(t0 * 1000)}"

                intent_str = intent.value if hasattr(intent, "value") else str(intent)
                push_data = await asyncio.wait_for(
                    run_oe_pipeline(
                        session_id=session_id,
                        message=message,
                        intent=intent_str,
                        confidence=confidence,
                        trace_id=trace_id,
                        state_snapshot=state_snapshot,
                        ai_executor=ai_executor,
                        alert_engine=alert_engine,
                        degrader=degrader,
                        breakers=breakers,
                        push_tracker=push_tracker,
                        redis_client=redis_client,
                        session_manager=session_manager,
                        sentiment=sentiment.value if sentiment and hasattr(sentiment, "value") else "neutral",
                    ),
                    timeout=5.0,
                )
                logger.debug("OE 编排完成(PydanticAI): session=%s elapsed=%.1fs", session_id, time.monotonic() - t0)
                return push_data
            except (TimeoutError, Exception) as e:
                logger.debug("PydanticAI OE Pipeline 不可用, 降级: %s", e)

        # 降级: 同步编排器
        orchestrator = getattr(app.state, "assist_orchestrator", None)
        if orchestrator:
            try:
                push_msg = await asyncio.wait_for(
                    orchestrator.process(session_id=session_id, message=message),
                    timeout=5.0,
                )
                if push_msg:
                    push_msg["session_id"] = session_id
                    logger.debug("OE 编排完成(同步): session=%s elapsed=%.1fs", session_id, time.monotonic() - t0)
                    return push_msg
            except (TimeoutError, Exception) as e:
                logger.debug("同步编排失败: %s", e)

    except Exception:
        logger.exception("OE 管道异常: session=%s", session_id)

    return None


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
    updated_state = await session_manager.transition_phase(
        session_id=body.session_id,
        new_phase=phase,
        new_sub_phase=new_sub_phase,
        reason=reason,
    )

    # 转接场景：更新 agent_id（用 patch_state 避免全量覆写和 CAS 异常）
    if body.agent_id and updated_state.agent_id != body.agent_id:
        try:
            await session_manager.patch_state(
                session_id=body.session_id,
                expected_version=updated_state.version,
                patches={"agent_id": body.agent_id},
                writer="session_update",
            )
        except Exception:
            logger.warning("agent_id 更新失败: session=%s", body.session_id)

    # ENDED 时清理 WebSocket 和超时守卫
    if phase == SessionPhase.ENDED:
        ws_pool: dict[str, WebSocket] = getattr(app.state, WS_POOL_KEY, {})
        ws = ws_pool.pop(body.session_id, None)
        if ws:
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "session_ended", "session_id": body.session_id})
                await ws.close()
        # 清理静音检测
        _silence_tasks.discard(body.session_id)
        watcher = _silence_watchers.pop(body.session_id, None)
        if watcher and not watcher.done():
            watcher.cancel()

    logger.info(
        "Session %s updated to %s:%s", body.session_id, phase.value, new_sub_phase.value if new_sub_phase else "-"
    )
    return SessionUpdateResponse(status="ok")


# ── Analyze（star-connection 回调，生产级入口）──


@router.post("/analyze")
async def analyze_message(body: AnalyzeRequest, request: Request):
    """star-connection 回调：分析客户消息并推送辅助结果给坐席

    由 star-connection 在收到客户消息时调用。
    SmartCS 执行完整分析链路后将结果推送到对应 WebSocket。
    优先通过 OE Pipeline 编排，不可用时降级到旧 AssistOrchestrator。
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
            intent_result, _entities, sentiment_val, source = await asyncio.wait_for(
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

    # 2. 执行编排（优先 PydanticAI OE Pipeline > 同步编排器）
    t0 = time.monotonic()
    push_data = None

    state_snapshot: dict = {}
    session_manager = getattr(app.state, "session_manager", None)
    if session_manager:
        snapshot = await session_manager.read_state(body.session_id)
        state_snapshot = snapshot or {"last_confidence": confidence}

    # 优先: PydanticAI OE Pipeline
    ai_executor = getattr(app.state, "ai_executor", None)
    if ai_executor is not None:
        try:
            from smartcs.services.common.oe_pipeline import load_push_tracker, run_oe_pipeline

            redis_client = getattr(app.state, "redis_client", None)
            alert_engine = getattr(app.state, "alert_engine", None)
            degrader = getattr(app.state, "degradation_mgr", None)
            breakers = getattr(app.state, "oe_breakers", None)
            push_tracker = await load_push_tracker(body.session_id, redis_client)
            trace_id = f"{body.session_id}-{int(t0 * 1000)}"

            push_data = await asyncio.wait_for(
                run_oe_pipeline(
                    session_id=body.session_id,
                    message=body.message,
                    intent=intent.value,
                    confidence=confidence,
                    trace_id=trace_id,
                    state_snapshot=state_snapshot,
                    ai_executor=ai_executor,
                    alert_engine=alert_engine,
                    degrader=degrader,
                    breakers=breakers,
                    push_tracker=push_tracker,
                    redis_client=redis_client,
                    session_manager=session_manager,
                    sentiment=sentiment_val.value if sentiment_val and hasattr(sentiment_val, "value") else "neutral",
                ),
                timeout=5.0,
            )
        except TimeoutError:
            logger.warning("OE Pipeline 超时 session=%s", body.session_id)
        except Exception as e:
            logger.warning("OE Pipeline 异常: %s，降级到同步编排器", e)

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
        body.session_id,
        intent.value,
        confidence,
        elapsed,
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
            body.session_id,
            len(ws_pool),
        )

    return {"status": "ok", "intent": intent.value, "confidence": confidence}


# ── Review（话后小结，对应 AG_REVIEWING 阶段）──


class ReviewRequest(BaseModel):
    """话后小结审核请求"""

    session_id: str
    agent_id: str


class ReviewResponse(BaseModel):
    """话后小结响应"""

    summary_id: str
    session_id: str
    customer_demand: str = ""
    problem_category: str = ""
    solution_provided: str = ""
    resolution_status: str = ""
    sentiment: str = "neutral"
    key_info: dict = {}
    status: str = "draft"


@router.post("/review/generate", response_model=ReviewResponse)
async def generate_review(body: ReviewRequest, request: Request):
    """生成话后小结

    AG_REVIEWING 阶段调用 LLM 自动生成话后小结，供坐席审核。
    """
    app = request.app
    session_manager = getattr(app.state, "session_manager", None)
    if session_manager is None:
        raise SmartCSError(code=5001, message="会话管理器未就绪")

    # 校验会话状态
    state = await session_manager.get_session(body.session_id)
    if state is None:
        raise SmartCSError(code=2001, message="会话不存在")
    if state.current_phase != SessionPhase.AGENT or state.sub_phase != SessionSubPhase.AG_REVIEWING:
        raise SmartCSError(code=2001, message="会话不在话后审核阶段")

    llm_client = getattr(app.state, "llm_client", None)
    summary = await generate_call_summary(body.session_id, session_manager, llm_client)

    # 推送小结给坐席
    ws_pool: dict[str, WebSocket] = getattr(app.state, WS_POOL_KEY, {})
    ws = ws_pool.get(body.session_id)
    if ws is not None:
        try:
            await ws.send_json(
                {
                    "type": "call_summary",
                    "session_id": body.session_id,
                    "payload": summary.model_dump(mode="json"),
                }
            )
        except Exception:
            logger.warning("小结推送失败: session=%s", body.session_id)

    return ReviewResponse(
        summary_id=summary.summary_id,
        session_id=summary.session_id,
        customer_demand=summary.customer_demand,
        problem_category=summary.problem_category,
        solution_provided=summary.solution_provided,
        resolution_status=summary.resolution_status,
        sentiment=summary.sentiment.value,
        key_info=summary.key_info,
        status="draft",
    )


class ReviewSubmitRequest(BaseModel):
    """话后小结提交请求"""

    session_id: str
    agent_id: str
    summary_id: str
    customer_demand: str | None = None
    problem_category: str | None = None
    solution_provided: str | None = None
    resolution_status: str | None = None
    sentiment: str | None = None
    key_info: dict | None = None


@router.post("/review/submit")
async def submit_review(body: ReviewSubmitRequest, request: Request):
    """坐席审核并提交话后小结

    提交后结束会话 (AG_REVIEWING → ENDED)。
    """
    app = request.app
    session_manager = getattr(app.state, "session_manager", None)
    if session_manager is None:
        raise SmartCSError(code=5001, message="会话管理器未就绪")

    await session_manager.transition_phase(
        body.session_id,
        SessionPhase.ENDED,
        reason="completed",
    )

    logger.info("话后小结已提交: session=%s agent=%s", body.session_id, body.agent_id)
    return {"status": "ok", "session_id": body.session_id, "summary_id": body.summary_id}


# ── Hold（坐席保持，对应 AG_ON_HOLD 阶段）──


class HoldRequest(BaseModel):
    """坐席保持请求"""

    session_id: str
    agent_id: str
    reason: str = ""


@router.post("/hold")
async def hold_session(body: HoldRequest, request: Request):
    """坐席保持（AG_ACTIVE → AG_ON_HOLD）

    进入保持后启动静音检测：60 秒无客户消息时推送提醒给坐席。
    """
    app = request.app
    session_manager = getattr(app.state, "session_manager", None)
    if session_manager is None:
        raise SmartCSError(code=5001, message="会话管理器未就绪")

    await session_manager.transition_phase(
        body.session_id,
        SessionPhase.AGENT,
        new_sub_phase=SessionSubPhase.AG_ON_HOLD,
        reason=body.reason or "agent_hold",
    )

    # 启动静音检测
    _silence_tasks.add(body.session_id)
    silence_task = asyncio.create_task(
        _silence_detector(app, body.session_id, body.agent_id, interval=60.0),
    )
    _silence_watchers[body.session_id] = silence_task
    silence_task.add_done_callback(lambda t: _silence_watchers.pop(body.session_id, None))

    logger.info("坐席保持: session=%s agent=%s", body.session_id, body.agent_id)
    return {"status": "ok", "sub_phase": "agent:on_hold"}


class ResumeRequest(BaseModel):
    """坐席恢复请求"""

    session_id: str
    agent_id: str


@router.post("/resume")
async def resume_session(body: ResumeRequest, request: Request):
    """坐席恢复（AG_ON_HOLD → AG_ACTIVE）

    取消静音检测，恢复会话。
    """
    app = request.app
    session_manager = getattr(app.state, "session_manager", None)
    if session_manager is None:
        raise SmartCSError(code=5001, message="会话管理器未就绪")

    await session_manager.transition_phase(
        body.session_id,
        SessionPhase.AGENT,
        new_sub_phase=SessionSubPhase.AG_ACTIVE,
        reason="agent_resume",
    )

    # 取消静音检测
    _silence_tasks.discard(body.session_id)
    watcher = _silence_watchers.pop(body.session_id, None)
    if watcher and not watcher.done():
        watcher.cancel()

    logger.info("坐席恢复: session=%s agent=%s", body.session_id, body.agent_id)
    return {"status": "ok", "sub_phase": "agent:active"}


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


# H2: 反馈延迟确认缓冲区（3 秒内可撤销）— 已迁移至 Redis
# _feedback_tasks 仅保持 asyncio.Task 引用防止 GC，缓冲数据在 Redis 中
_feedback_tasks: set[asyncio.Task] = set()

# 反馈缓冲 Redis key 前缀 + TTL
_FEEDBACK_KEY_PREFIX = "smartcs:assist:feedback"
_FEEDBACK_BUFFER_TTL = 10  # 秒（3s 延迟 + 7s 安全余量）

# 静音检测：ON_HOLD 期间无客户消息时提醒坐席
# per-WebSocket-connection 状态，连接断开时自动清理
_silence_tasks: set[str] = set()
_silence_watchers: dict[str, asyncio.Task] = {}


async def _silence_detector(app: object, session_id: str, agent_id: str, interval: float = 60.0) -> None:
    """ON_HOLD 期间的静音检测：interval 秒后推送提醒"""
    try:
        await asyncio.sleep(interval)
    except asyncio.CancelledError:
        return

    # 检查是否仍在保持状态
    if session_id not in _silence_tasks:
        return
    _silence_tasks.discard(session_id)

    # 推送提醒
    ws_pool: dict[str, WebSocket] = getattr(app.state, WS_POOL_KEY, {})  # type: ignore[attr-defined]
    ws = ws_pool.get(session_id)
    if ws is not None:
        try:
            await ws.send_json(
                {
                    "type": "silence_alert",
                    "session_id": session_id,
                    "message": f"客户已保持 {interval:.0f} 秒无消息",
                }
            )
        except Exception:
            logger.warning("静音提醒推送失败: session=%s", session_id)


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

    # H2: 缓冲反馈到 Redis，3 秒后才提交到状态管理器
    buffer_key = f"{_FEEDBACK_KEY_PREFIX}:{body.session_id}:{body.agent_id}"
    feedback_data = {
        "action": body.action,
        "confidence": confidence,
        "modify_fields": body.modify_fields,
        "agent_id": body.agent_id,
    }
    redis_client = getattr(app.state, "redis_client", None)
    if redis_client:
        await redis_client.setex(
            buffer_key,
            _FEEDBACK_BUFFER_TTL,
            json.dumps(feedback_data, ensure_ascii=False),
        )

    # 启动延迟提交任务
    feedback_task = asyncio.create_task(
        _commit_feedback_after_delay(
            app,
            body.session_id,
            buffer_key,
            feedback_data,
            delay=3.0,
        )
    )
    _feedback_tasks.add(feedback_task)
    feedback_task.add_done_callback(_feedback_tasks.discard)

    logger.info(
        "反馈(缓冲) session=%s agent=%s action=%s confidence=%.1f",
        body.session_id,
        body.agent_id,
        body.action,
        confidence,
    )

    return {"status": "ok", "action": body.action, "confidence": confidence, "delayed_commit": True}


@router.post("/feedback/undo")
async def undo_feedback(body: FeedbackRequest, request: Request):
    """H2: 撤销缓冲中的反馈

    在 3 秒延迟期内，坐席可以撤销反馈。
    """
    redis_client = getattr(request.app.state, "redis_client", None)
    buffer_key = f"{_FEEDBACK_KEY_PREFIX}:{body.session_id}:{body.agent_id}"
    if redis_client:
        deleted = await redis_client.delete(buffer_key)
        if deleted:
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
    """H2: 延迟提交反馈到 Redis 状态管理器"""
    await asyncio.sleep(delay)

    # 检查 Redis 中是否仍存在（未被撤销）
    redis_client = getattr(app, "state", None)  # type: ignore[attr-defined]
    if redis_client is None:
        return
    redis_client = getattr(redis_client, "redis_client", None)
    if redis_client is None:
        return

    raw = await redis_client.get(buffer_key)
    if raw is None:
        return  # 已被撤销或过期

    # 从 Redis 删除（防止重复提交）
    await redis_client.delete(buffer_key)

    # 写入反馈到会话状态（统一状态层，使用 session_manager）
    app_state = getattr(app, "state", None)  # type: ignore[attr-defined]
    if app_state is None:
        return
    session_manager = getattr(app_state, "session_manager", None)
    if session_manager is None:
        return

    try:
        state = await session_manager.read_state(session_id)  # type: ignore[attr-defined]
        if state:
            await session_manager.patch_state(  # type: ignore[attr-defined]
                session_id=session_id,
                expected_version=state.get("version", 1),
                patches={
                    "last_feedback": feedback_data,
                },
                writer=f"feedback:{feedback_data.get('agent_id', '')}",
            )
    except Exception as e:
        logger.warning("反馈提交失败: session=%s error=%s", session_id, e)


# ── WebSocket (per-session, 测试/开发用) ──


async def _handle_ws_notify(websocket: WebSocket, app, session_id: str) -> None:
    """监听 Redis Pub/Sub 频道, 处理 star-conn notify 消息

    替代旧的 _notify_session_worker 循环。
    串行性由 async for 循环自然保证: 上一条处理完才取下一条。
    """
    redis_client = getattr(app.state, "redis_client", None)
    if redis_client is None:
        return

    channel = f"smartcs:assist:notify:{session_id}"
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)

    try:
        async for msg in pubsub.listen():
            if msg["type"] == "message":
                try:
                    item = json.loads(msg["data"])
                    await _process_notify_message(app, session_id, websocket, item)
                except Exception:
                    logger.debug("notify 消息处理异常: session=%s", session_id)
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel)
        with contextlib.suppress(Exception):
            await pubsub.aclose()


async def _handle_ws_client_simple(websocket: WebSocket, session_id: str) -> None:
    """无编排器时的简化客户端消息处理"""
    while True:
        raw = await websocket.receive_text()
        if raw == "ping":
            await websocket.send_text("pong")
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send_json({"type": "error", "message": "无效的 JSON"})
            continue
        msg_type = data.get("type", "")
        if msg_type == "ping":
            await websocket.send_json({"type": "pong"})
        elif msg_type == "customer_message":
            await websocket.send_json(
                {
                    "type": "assist_push",
                    "session_id": session_id,
                    "trigger": "customer_message",
                    "payload": {
                        "scripts": [],
                        "knowledge": [],
                        "alerts": [],
                        "recommendations": [],
                    },
                }
            )


@router.websocket("/ws/{session_id}")
async def session_websocket(websocket: WebSocket, session_id: str):
    """按会话建连的 WebSocket（测试 / 开发用）

    生产环境使用 /ws/agent/{agent_id} 持久连接。
    本端点用于端到端测试和快速验证，连接后立即发送 assist_ready。

    并发运行两个任务:
    - 客户端消息处理 (ping/pong, customer_message)
    - Redis Pub/Sub 监听 (star-conn notify → OE 编排 → WS 推送)
    """
    await websocket.accept()
    app = websocket.app

    # 注册到连接池
    ws_pool: dict[str, WebSocket] = getattr(app.state, WS_POOL_KEY, {})
    ws_pool[session_id] = websocket

    # 发送就绪确认
    await websocket.send_json(
        {
            "type": "assist_ready",
            "session_id": session_id,
            "message": "坐席辅助服务就绪",
        }
    )

    orchestrator = getattr(app.state, "assist_orchestrator", None)
    session_manager = getattr(app.state, "session_manager", None)

    # 从 SessionState 加载情绪历史（多实例一致，断线重连不丢）
    sentiment_history: list[SentimentLabel] = []
    if session_manager:
        sentiment_history = await _load_sentiment_history(session_manager, session_id)

    # Task 1: 客户端消息处理
    if orchestrator is not None:
        client_task = asyncio.create_task(_handle_messages(websocket, orchestrator, session_id, sentiment_history))
    else:
        client_task = asyncio.create_task(_handle_ws_client_simple(websocket, session_id))

    # Task 2: notify 消息处理 (Redis Pub/Sub)
    notify_task = asyncio.create_task(_handle_ws_notify(websocket, app, session_id))

    try:
        done, pending = await asyncio.wait(
            [client_task, notify_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    except WebSocketDisconnect:
        logger.info("Session WS 断开: session=%s", session_id)
    finally:
        ws_pool.pop(session_id, None)


# ── WebSocket (per-agent, 持久连接) ──


@router.websocket("/ws/agent/{agent_id}")
async def assist_websocket(websocket: WebSocket, agent_id: str):
    """坐席辅助 WebSocket — 按坐席建连, 持久连接

    坐席登录时建连, 生命周期与上班周期对齐。
    会话上下文通过消息中 session_id 字段区分。

    双向消息:
      → AgentUI: assist_push / call_summary / silence_alert / session_timeout
      ← AgentUI: session_activated / agent_message
    """
    await websocket.accept()

    app = websocket.app

    # 注册到连接池 (agent_id → WebSocket)
    ws_pool: dict[str, WebSocket] = getattr(app.state, WS_POOL_KEY, {})
    ws_pool[agent_id] = websocket
    logger.info("Agent WS 注册: agent=%s, pool_size=%d", agent_id, len(ws_pool))

    # 发送连接确认
    await websocket.send_json(
        {
            "type": "connected",
            "agent_id": agent_id,
        }
    )

    # 心跳任务
    heartbeat_task = asyncio.create_task(_heartbeat(websocket))

    try:
        await _handle_agent_messages(websocket, app, agent_id)
    except WebSocketDisconnect:
        logger.info("Agent WS 断开: agent=%s", agent_id)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        ws_pool.pop(agent_id, None)
        logger.info("Agent WS 清理: agent=%s, pool_size=%d", agent_id, len(ws_pool))


async def _handle_agent_messages(websocket: WebSocket, app, agent_id: str):
    """处理坐席 WS 消息"""
    session_manager = getattr(app.state, "session_manager", None)

    async for raw in websocket.iter_text():
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue

        msg_type = msg.get("type", "")

        if msg_type == "session_activated":
            # 坐席接听了某个会话 → 激活 Assist
            session_id = msg.get("session_id", "")
            if session_id and session_manager:
                try:
                    state = await session_manager.get_session(session_id)
                    if state and state.sub_phase == SessionSubPhase.AG_QUEUED:
                        await session_manager.transition_phase(
                            session_id,
                            SessionPhase.AGENT,
                            new_sub_phase=SessionSubPhase.AG_ACTIVE,
                            reason="agent_accepted",
                        )
                        # 推送客户画像 + 转接摘要 + 已知实体 + 对话摘要
                        summary_info = {
                            "transfer_reason": state.transfer_reason,
                            "transfer_summary": state.transfer_summary,
                            "conversation_summary": state.conversation_summary,
                            "vip_level": state.vip_level,
                            "card_types": state.card_types,
                            "known_entities": [{"type": e.entity_type, "value": e.value} for e in state.last_entities]
                            if state.last_entities
                            else [],
                            "last_intent": state.last_intent.value if state.last_intent else None,
                            "turns": [{"speaker": t.speaker, "content": t.content} for t in state.turns[-20:]],
                        }
                        await websocket.send_json(
                            {
                                "type": "assist_ready",
                                "session_id": session_id,
                                "agent_id": agent_id,
                                "summary": summary_info,
                            }
                        )
                        logger.info("会话激活: session=%s agent=%s", session_id, agent_id)
                except (SessionNotFoundError, InvalidTransitionError) as e:
                    logger.debug("会话激活跳过: %s", e)

        elif msg_type == "agent_message":
            # 坐席发送了回复 → 合规检测 + 隐式反馈推断
            session_id = msg.get("session_id", "")
            content = msg.get("content", "")
            if session_id and content:
                # 合规检测
                alert_engine = getattr(app.state, "alert_engine", None)
                if alert_engine:
                    try:
                        alerts = await alert_engine.check(session_id, content, speaker="agent")
                        for alert in alerts:
                            await websocket.send_json(
                                {
                                    "type": "assist_push",
                                    "session_id": session_id,
                                    "payload": {
                                        "alerts": [
                                            {
                                                "level": alert.level,
                                                "category": alert.category,
                                                "message": alert.message,
                                                "rule_id": alert.rule_id,
                                            }
                                        ],
                                    },
                                }
                            )
                    except Exception:
                        logger.debug("合规检测异常: session=%s", session_id)

                # 隐式反馈推断: 比较坐席实际回复与推送话术的相似度
                _infer_feedback(app, session_id, content)


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

    has_critical = any(a.level == "critical" for a in push_msg.payload.alerts)
    if has_critical or not orchestrator.should_throttle(session_id):  # type: ignore[attr-defined]
        await websocket.send_json(push_msg.model_dump(mode="json"))
        logger.debug("推送至 session=%s, elapsed=%.1fms", session_id, elapsed_ms)
    else:
        logger.debug("节流跳过 session=%s", session_id)


def _infer_feedback(app, session_id: str, agent_content: str) -> None:
    """隐式反馈推断: 比较坐席实际回复与推送话术的相似度

    在后台执行，不阻塞 WS 消息循环。
    推断规则: 坐席回复与某条话术的编辑距离 < 阈值 → accept (conf=1.0)
             部分匹配 → partial_accept (conf=0.3)
             无匹配 → ignore (不做记录, 非负反馈)
    """

    async def _do_infer():
        try:
            # 获取最近一次推送的话术
            script_service = getattr(app.state, "script_service", None)
            if not script_service:
                return

            # 简化的相似度: 与话术库的编辑距离
            from difflib import SequenceMatcher

            scripts = getattr(script_service, "_scripts_cache", [])
            best_ratio = 0.0
            for script in scripts:
                ratio = SequenceMatcher(None, agent_content, script.get("content", "")).ratio()
                best_ratio = max(best_ratio, ratio)

            if best_ratio > 0.8:
                # 推断为采纳
                logger.debug("隐式反馈推断: session=%s action=accept ratio=%.2f", session_id, best_ratio)
            elif best_ratio > 0.4:
                logger.debug("隐式反馈推断: session=%s action=partial_accept ratio=%.2f", session_id, best_ratio)
            # ratio < 0.4: 不做记录

        except Exception:
            logger.debug("隐式反馈推断异常: session=%s", session_id)

    task = asyncio.create_task(_do_infer())
    _feedback_tasks.add(task)
    task.add_done_callback(_feedback_tasks.discard)


async def _heartbeat(websocket: WebSocket, interval: float = 15.0):
    """心跳发送"""
    while True:
        await asyncio.sleep(interval)
        try:
            await websocket.send_json({"type": "heartbeat"})
        except Exception:
            break


# ── Notify 启动/停止 (lifespan 调用) ──


async def start_notify_worker(app) -> None:
    """notify 系统初始化（Redis Pub/Sub 模式下无需启动分发协程）

    notify 消息通过 Redis Pub/Sub 直接路由到 WebSocket handler，
    不再需要全局队列和分发协程。保留此函数以兼容 lifespan 调用。
    """
    logger.info("Notify 系统 (Redis Pub/Sub) 已就绪")


async def stop_notify_worker(app) -> None:
    """notify 系统清理（Redis Pub/Sub 模式下无需停止分发协程）"""
    logger.info("Notify 系统已停止")


# ── 坐席 KB 搜索 ──


class KbSearchRequest(BaseModel):
    """坐席知识库搜索请求"""

    query: str
    top_k: int = 5
    search_type: str = "hybrid"


@router.post("/kb/search")
async def kb_search(body: KbSearchRequest, request: Request):
    """坐席主动搜索知识库"""
    from smartcs.services.common.deps import (
        get_embedding_breaker,
        get_es_breaker,
        get_es_client,
        get_milvus_breaker,
        get_milvus_collection,
        get_reranker_provider,
    )
    from smartcs.services.common.retrieval import retrieve
    from smartcs.shared.models import RetrieveRequest

    app = request.app
    es_client = get_es_client(request)
    milvus_collection = get_milvus_collection(request)
    embedding_breaker = get_embedding_breaker(request)
    reranker = get_reranker_provider(request)
    es_breaker = get_es_breaker(request)
    milvus_breaker = get_milvus_breaker(request)

    embedding_provider = embedding_breaker.provider if embedding_breaker and embedding_breaker.is_available else None
    effective_es = es_client if (es_client and es_breaker and es_breaker.allow_request()) else None
    effective_milvus = (
        milvus_collection if (milvus_collection and milvus_breaker and milvus_breaker.allow_request()) else None
    )

    result = await retrieve(
        request=RetrieveRequest(
            query=body.query,
            top_k=body.top_k,
            search_type=body.search_type,
        ),
        es_client=effective_es,
        milvus_collection=effective_milvus,
        embedding_provider=embedding_provider,
        reranker=reranker,
    )
    return result


# ── 转回 Bot ──


class TransferToBotRequest(BaseModel):
    """转回 Bot 请求"""

    session_id: str
    agent_id: str
    reason: str = "agent_transfer_back"


@router.post("/transfer-to-bot")
async def transfer_to_bot(body: TransferToBotRequest, request: Request):
    """坐席将会话转回 Bot 自助服务"""
    session_manager = getattr(request.app.state, "session_manager", None)
    if session_manager is None:
        raise SmartCSError(code=5001, message="会话管理器未就绪")

    await session_manager.transition_phase(
        session_id=body.session_id,
        new_phase=SessionPhase.BOT,
        new_sub_phase=SessionSubPhase.BOT_ACTIVE,
        reason=body.reason,
    )

    # 通知坐席 WebSocket
    ws_pool: dict[str, WebSocket] = getattr(request.app.state, WS_POOL_KEY, {})
    ws = ws_pool.get(body.session_id) or ws_pool.get(body.agent_id)
    if ws:
        with contextlib.suppress(Exception):
            await ws.send_json(
                {
                    "type": "session_transferred",
                    "session_id": body.session_id,
                    "transferred_to": "bot",
                    "message": "会话已转回机器人",
                }
            )

    logger.info("会话转回 Bot: session=%s agent=%s", body.session_id, body.agent_id)
    return {"status": "ok", "session_id": body.session_id, "transferred_to": "bot"}
