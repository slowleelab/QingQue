"""机器人服务 HTTP API 路由

消息通道: Redis Streams (Consumer Group + PEL) → at-least-once 保证
结果通知: Redis Pub/Sub 即时唤醒 + Redis response key 作为结果载体
消费模式: XREADGROUP 10 条一批 → create_task 异步并行, per-session Lock 保序
过载保护: Semaphore(10) → 满荷走固定话术快速兜底, 不拒客
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import uuid as uuid_module
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from smartcs.services.common.audit import update_chat_message, write_chat_message
from smartcs.services.common.deps import (
    DbSession,
    EmbeddingBreakerDep,
    ESBreakerDep,
    ESClientDep,
    MilvusBreakerDep,
    MilvusCollectionDep,
    MinioClientDep,
    RerankerProviderDep,
)
from smartcs.services.common.retrieval import retrieve
from smartcs.services.common.rule_loader import RuleLoader
from smartcs.shared.config import get_settings
from smartcs.shared.exceptions import DocumentFormatError
from smartcs.shared.models import (
    ChatSendRequest,
    ChatSendResponse,
    RetrieveRequest,
    RetrieveResponse,
    SessionPhase,
    SessionSubPhase,
)
from smartcs.shared.orm_models import ChatMessageStatus, KbDocStatus, KbDocument, KbSourceType

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bot"])

# ── Redis 键 ────────────────────────────────────────────────────
CHAT_STREAM_KEY = "smartcs:chat:stream"
DEAD_LETTER_KEY = "smartcs:chat:dead_letter"
CONSUMER_GROUP = "bot-group"
RESPONSE_KEY_PREFIX = "smartcs:response"
NOTIFY_CHANNEL_PREFIX = "smartcs:notify"
RESPONSE_TTL = 120
STREAM_MAXLEN = 10000  # Stream 最大保留条数（近似裁剪，防止无限增长）
DEAD_LETTER_MAXLEN = 5000  # 死信队列最大保留条数
MAX_RETRY_COUNT = 3  # 消息最大重试次数，超过后转入死信队列

# ── 并发控制 ────────────────────────────────────────────────────
_agent_semaphore: asyncio.Semaphore | None = None

# per-session Queue + Worker: 每会话一个独享协程, 无锁串行消费, 可跳过过期消息
_session_queues: dict[str, asyncio.Queue] = {}
_session_active: dict[str, bool] = {}

# 数据库会话工厂（在 start_bot_worker 中注入，供审计落库使用）
_db_session_factory = None
# 规则加载器（Phase 3: DB+Redis 热加载，替代硬编码 _FAST_INTENT_PATTERNS）
_rule_loader: RuleLoader = RuleLoader()

# ── 快速兜底话术 (Semaphore 满荷时使用) ──────────────────────────
# 银行不能拒客, 满荷走快速通道: regex 意图匹配 <5ms → 固定话术 → source=fast_reply
_FAST_REPLIES: dict[str, str] = {
    "lost_card": "挂失为紧急业务，正在为您优先处理，请稍候。如超过 10 秒未回复，请直接输入'转人工'。",
    "complaint": "您的投诉已记录，正在转接人工处理。",
    "bill_query": "当前咨询量较大，账单查询结果稍后返回，也可输入'转人工'联系客服。",
    "limit_query": "您的问题正在处理中，预计 30 秒内回复。",
    "default": "当前咨询量较大，请稍候或输入'转人工'。",
}

# 快速意图匹配 regex (仅用于满荷兜底, 不做精确分类)
_FAST_INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("lost_card", re.compile(r"挂失|丢卡|卡丢了|卡不见了")),
    ("complaint", re.compile(r"投诉")),
    ("bill_query", re.compile(r"账单|消费|扣款|还款|欠款")),
    ("limit_query", re.compile(r"额度|提额|降额|信用额度")),
]


def _quick_intent_match(message: str) -> str:
    """快速意图匹配 (Phase 3: RuleLoader DB+Redis 热加载, <5ms)"""
    intent, _ = _rule_loader.match(message)
    return intent


# 支持的文件扩展名 → KbSourceType 映射
_EXT_TO_SOURCE: dict[str, str] = {
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".html": "HTML",
    ".md": "MARKDOWN",
    ".txt": "TXT",
    ".xlsx": "XLSX",
}
_ALLOWED_EXTENSIONS = set(_EXT_TO_SOURCE.keys())


# ── Helpers ─────────────────────────────────────────────────────


def _build_poll_json(
    *,
    status: str,
    reply: str | None = None,
    intent: str | None = None,
    confidence: float = 0.0,
    source: str | None = None,
    position: int | None = None,
    est_wait: str | None = None,
    suggestion: str | None = None,
) -> dict:
    has_message = status == "done" and reply is not None and len(reply) > 0
    data: dict = {"status": status, "has_message": has_message}
    if reply is not None:
        data["reply"] = reply
    if intent is not None:
        data["intent"] = intent
    if confidence:
        data["confidence"] = confidence
    if source:
        data["source"] = source
    if position is not None:
        data["position"] = position
    if est_wait:
        data["est_wait"] = est_wait
    if suggestion:
        data["suggestion"] = suggestion
    return data


async def _finish_message(
    redis_client,
    session_id: str,
    reply: str,
    intent: str | None = None,
    confidence: float = 0.0,
    source: str = "fallback",
) -> None:
    """写入 response key + 发布 Pub/Sub 通知"""
    # 安全过滤：对 Bot 回复进行敏感词过滤
    from smartcs.shared.safety import safety_filter

    reply = safety_filter.filter_output(reply)

    payload = _build_poll_json(
        status="done",
        reply=reply,
        intent=intent,
        confidence=confidence,
        source=source,
    )
    response_key = f"{RESPONSE_KEY_PREFIX}:{session_id}"
    notify_channel = f"{NOTIFY_CHANNEL_PREFIX}:{session_id}"

    await redis_client.setex(response_key, RESPONSE_TTL, json.dumps(payload, ensure_ascii=False))
    await redis_client.publish(notify_channel, "ready")


# ── Consumer group 初始化 ───────────────────────────────────────


async def _init_stream_group(redis_client) -> None:
    """确保 stream 和 consumer group 已创建"""
    try:
        await redis_client.xgroup_create(CHAT_STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
        logger.info("Stream consumer group 已创建: %s / %s", CHAT_STREAM_KEY, CONSUMER_GROUP)
    except Exception:
        # XGROUP CREATE 在 group 已存在时抛错, 忽略
        logger.debug("Stream consumer group 已存在: %s / %s", CHAT_STREAM_KEY, CONSUMER_GROUP)


# ── XAUTOCLAIM 兜底 ─────────────────────────────────────────────


async def _claim_stale(redis_client, agent) -> None:
    """后台协程: 定期认领超时未 XACK 的挂死消息

    带重试上限: 同一消息被认领超过 MAX_RETRY_COUNT 次后转入死信队列，
    防止异常消息无限循环占用资源。
    """
    consumer_name = f"bot-claim-{os.getpid()}"
    retry_counter_key = "smartcs:chat:retry_count"

    while True:
        try:
            await asyncio.sleep(30)
            claimed = await redis_client.xautoclaim(
                CHAT_STREAM_KEY,
                CONSUMER_GROUP,
                consumer_name,
                min_idle_time=60000,  # 60s 超时
                count=10,
            )
            if claimed and claimed[1]:
                for msg_id, fields in claimed[1]:
                    # 检查重试次数
                    retry_count = await redis_client.hincrby(retry_counter_key, str(msg_id), 1)

                    if retry_count > MAX_RETRY_COUNT:
                        logger.error(
                            "消息超过最大重试次数 %d，转入死信队列: msg_id=%s",
                            MAX_RETRY_COUNT,
                            msg_id,
                        )
                        # 写入死信队列
                        await redis_client.xadd(
                            DEAD_LETTER_KEY,
                            {
                                "original_msg_id": str(msg_id),
                                "session_id": str(fields.get("session_id", "")),
                                "message": str(fields.get("message", "")),
                                "error": f"超过最大重试次数 {MAX_RETRY_COUNT}",
                                "retry_count": str(retry_count),
                                "timestamp": str(asyncio.get_event_loop().time()),
                            },
                            maxlen=DEAD_LETTER_MAXLEN,
                            approximate=True,
                        )
                        # ACK 丢弃消息 + 清理重试计数
                        await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
                        await redis_client.hdel(retry_counter_key, str(msg_id))
                        continue

                    logger.warning(
                        "XAUTOCLAIM 认领挂死消息 (retry %d/%d): msg_id=%s",
                        retry_count,
                        MAX_RETRY_COUNT,
                        msg_id,
                    )
                    fields["_enqueue_time"] = asyncio.get_event_loop().time()
                    asyncio.create_task(_dispatch_message(redis_client, agent, msg_id, fields))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("XAUTOCLAIM 异常")


# ── 消息处理核心 ────────────────────────────────────────────────


async def _dispatch_message(redis_client, agent, msg_id: str, fields: dict) -> None:
    """消息分发: 按 session_id 路由到 per-session Queue, 首次触发 Worker"""
    session_id = fields.get("session_id", "")

    if not session_id:
        logger.warning("消息字段不完整: msg_id=%s", msg_id)
        await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
        return

    q = _session_queues.setdefault(session_id, asyncio.Queue())

    # Worker 注册必须在 await 之前完成，防止竞态创建多个 Worker
    if session_id not in _session_active:
        _session_active[session_id] = True
        asyncio.create_task(_session_worker(session_id, q, redis_client, agent))

    await q.put((msg_id, fields))


async def _session_worker(
    session_id: str,
    q: asyncio.Queue,
    redis_client,
    agent,
) -> None:
    """per-session Worker: 独享协程, 串行消费, 无锁

    消费完所有消息后自动退出 (队列为空且等待超时), 避免泄漏。
    """
    from smartcs.shared.config import get_settings

    settings = get_settings()
    message_ttl = settings.bot.message_ttl_seconds

    try:
        while True:
            # 阻塞等待消息，同时设置空闲超时 (300s 无消息则退出 Worker)
            try:
                msg_id, fields = await asyncio.wait_for(q.get(), timeout=300)
            except TimeoutError:
                logger.debug("session worker 空闲退出: session=%s", session_id)
                break

            message = fields.get("message", "")
            enqueue_time = fields.get("_enqueue_time", 0.0)
            client_message_id = fields.get("message_id", "")
            customer_id = fields.get("customer_id", "")
            channel = fields.get("channel", "web")
            trace_raw = fields.get("_trace_context", "")
            trace_id = trace_raw.split(":")[0] if trace_raw else None

            # ── 审计落库：记录消息到达 ──
            if _db_session_factory and client_message_id:
                quick_intent = _quick_intent_match(message)
                await write_chat_message(
                    _db_session_factory,
                    session_id=session_id,
                    message_id=client_message_id,
                    content=message,
                    customer_id=customer_id,
                    channel=channel,
                    quick_intent=quick_intent,
                    trace_id=trace_id,
                )

            processing_start = asyncio.get_event_loop().time()

            # ── 从 Stream 消息中恢复 trace context, 链接 Worker Span 到 HTTP Span ──
            otel_token = None
            if trace_raw:
                try:
                    parts = trace_raw.split(":")
                    if len(parts) == 3:
                        from opentelemetry import context
                        from opentelemetry import trace as otel_trace
                        from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

                        tid = int(parts[0], 16)
                        sid = int(parts[1], 16)
                        flags = TraceFlags(int(parts[2], 16))
                        sc = SpanContext(trace_id=tid, span_id=sid, is_remote=True, trace_flags=flags)
                        parent_ctx = otel_trace.set_span_in_context(NonRecordingSpan(sc))
                        otel_token = context.attach(parent_ctx)
                except Exception:
                    pass

            try:
                # 1. 跳过过期消息
                now = asyncio.get_event_loop().time()
                if enqueue_time and (now - enqueue_time > message_ttl):
                    logger.debug("消息过期跳过: session=%s msg_id=%s", session_id, msg_id)
                    _metrics["to"] += 1
                    await _finish_message(
                        redis_client,
                        session_id,
                        "当前咨询量较大，回复超时，请重新发送或输入'转人工'。",
                        source="timeout",
                    )
                    await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
                    if _db_session_factory and client_message_id:
                        await update_chat_message(
                            _db_session_factory,
                            client_message_id,
                            processing_status=ChatMessageStatus.SKIPPED,
                            source="timeout",
                            processing_duration_ms=int((now - enqueue_time) * 1000),
                        )
                    continue

                # 2. 幂等检查
                response_key = f"{RESPONSE_KEY_PREFIX}:{session_id}"
                if await redis_client.exists(response_key):
                    logger.debug("幂等跳过: session=%s", session_id)
                    await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
                    return

                # 3. Semaphore 满荷 → 快速兜底
                if _agent_semaphore and _agent_semaphore.locked():
                    intent_hint = _quick_intent_match(message)
                    reply = _FAST_REPLIES.get(intent_hint, _FAST_REPLIES["default"])
                    await _finish_message(redis_client, session_id, reply, intent=intent_hint, source="fast_reply")
                    await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
                    _metrics["fr"] += 1
                    if _db_session_factory and client_message_id:
                        duration = int((asyncio.get_event_loop().time() - processing_start) * 1000)
                        await update_chat_message(
                            _db_session_factory,
                            client_message_id,
                            processing_status=ChatMessageStatus.DONE,
                            intent=intent_hint,
                            source="fast_reply",
                            processing_duration_ms=duration,
                        )
                    logger.info("快速兜底: session=%s intent=%s", session_id, intent_hint)
                    return

                # 4. 标准 Agent 处理路径
                # 4a. 调用前队列检查: peek 队列并 drain 排队消息, 合并后一次 LLM 调用
                merged_message_ids: list[str] = []
                merged_contents: list[str] = []

                if not q.empty():
                    while True:
                        try:
                            pending_msg_id, pending_fields = q.get_nowait()
                        except asyncio.QueueEmpty:
                            break

                        pending_msg = pending_fields.get("message", "")
                        pending_enqueue = pending_fields.get("_enqueue_time", 0.0)
                        pending_client_id = pending_fields.get("message_id", "")

                        # 跳过过期排队消息
                        if pending_enqueue and (now - pending_enqueue > message_ttl):
                            await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, pending_msg_id)
                            _metrics["to"] += 1
                            if _db_session_factory and pending_client_id:
                                await update_chat_message(
                                    _db_session_factory,
                                    pending_client_id,
                                    processing_status=ChatMessageStatus.SKIPPED,
                                    source="timeout",
                                )
                            continue

                        # 审计落库: 被合并的消息
                        if _db_session_factory and pending_client_id:
                            await write_chat_message(
                                _db_session_factory,
                                session_id=session_id,
                                message_id=pending_client_id,
                                content=pending_msg,
                                customer_id=pending_fields.get("customer_id", ""),
                                channel=pending_fields.get("channel", "web"),
                                quick_intent=_quick_intent_match(pending_msg),
                                trace_id=(
                                    pending_fields.get("_trace_context", "").split(":")[0]
                                    if pending_fields.get("_trace_context")
                                    else None
                                ),
                            )

                        merged_message_ids.append(pending_client_id)
                        merged_contents.append(pending_msg)
                        await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, pending_msg_id)

                if merged_contents:
                    merged_message = message + "\n" + "\n".join(merged_contents)
                    _metrics["mg"] += len(merged_contents)
                    logger.info(
                        "队列合并: session=%s merged=%d total_len=%d",
                        session_id,
                        len(merged_contents),
                        len(merged_message),
                    )
                else:
                    merged_message = message

                # 4b. 标准 Agent (Semaphore.locked() 检查存在微竞态, 但 acquire 会挂起等待, 不丢数据)
                async with _agent_semaphore:
                    try:
                        await _run_agent(
                            redis_client,
                            agent,
                            session_id,
                            merged_message,
                            msg_id,
                            fields.get("message_id", ""),
                            merged_message_ids=merged_message_ids,
                        )
                    except Exception:
                        logger.exception("Agent 异常: session=%s msg_id=%s", session_id, msg_id)
                        await _finish_message(
                            redis_client,
                            session_id,
                            "系统处理您的请求时出现错误，请稍后再试。",
                            source="error_fallback",
                        )
                        if _db_session_factory and client_message_id:
                            duration = int((asyncio.get_event_loop().time() - processing_start) * 1000)
                            await update_chat_message(
                                _db_session_factory,
                                client_message_id,
                                processing_status=ChatMessageStatus.ERROR,
                                source="error_fallback",
                                processing_duration_ms=duration,
                                error_message="Agent 处理异常",
                            )
                        # 写入死信队列（便于后续排查和重试）
                        with contextlib.suppress(Exception):
                            await redis_client.xadd(
                                DEAD_LETTER_KEY,
                                {
                                    "original_msg_id": str(msg_id),
                                    "session_id": str(fields.get("session_id", "")),
                                    "message": str(fields.get("message", "")),
                                    "error": "Agent 处理异常",
                                    "timestamp": str(asyncio.get_event_loop().time()),
                                },
                                maxlen=DEAD_LETTER_MAXLEN,
                                approximate=True,
                            )
                    await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)

            finally:
                # 释放 trace context
                if otel_token is not None:
                    from opentelemetry import context

                    context.detach(otel_token)

    except asyncio.CancelledError:
        logger.debug("session worker 取消: session=%s", session_id)
        raise
    finally:
        _session_active.pop(session_id, None)
        _session_queues.pop(session_id, None)


async def _run_agent(
    redis_client,
    agent,
    session_id: str,
    message: str,
    msg_id: str,
    orig_message_id: str,
    merged_message_ids: list[str] | None = None,
) -> None:
    """标准 Agent 处理路径 (Semaphore 内)

    Args:
        merged_message_ids: 被合并的消息 ID 列表（调用前队列检查合并的消息）
    """
    session_manager = agent._session_manager

    # Session 获取或创建
    state = await session_manager.get_or_create(session_id)

    # 如果已进入 AGENT 阶段，跳过 bot 处理
    if state.current_phase.value == "agent":
        logger.info("会话已进入 AGENT 阶段, 跳过 bot: session=%s", session_id)
        await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
        return

    # Agent 编排
    result = await agent.run(session_id, message)

    intent = result.get("intent")
    primary_intent = intent.primary_intent if intent and hasattr(intent, "primary_intent") else None
    primary_confidence = intent.primary_confidence if intent and hasattr(intent, "primary_confidence") else 0.0
    is_transfer = result.get("should_transfer", False)
    transfer_reason = result.get("transfer_reason", "")
    reply = result.get("response", "抱歉，我暂时无法处理您的请求。")
    source = result.get("response_source", "fallback")
    entities = result.get("entities", [])

    # ── 保存对话历史（客户消息 + Bot 回复）──
    from uuid import uuid4 as _uuid4

    from smartcs.shared.models import DialogueTurn

    customer_turn = DialogueTurn(
        turn_id=_uuid4().hex,
        session_id=session_id,
        speaker="customer",
        content=message,
        intent=primary_intent,
        confidence=primary_confidence,
        entities=entities if isinstance(entities, list) else [],
    )
    bot_turn = DialogueTurn(
        turn_id=_uuid4().hex,
        session_id=session_id,
        speaker="bot",
        content=reply,
        intent=primary_intent,
        confidence=primary_confidence,
        response_source=source,
        retrieval_context=result.get("retrieval_context", ""),
    )
    with contextlib.suppress(Exception):
        await session_manager.add_turn(session_id, customer_turn, intent=intent)
        await session_manager.add_turn(session_id, bot_turn, intent=intent)

    # 转人工处理
    if is_transfer:
        transfer_url = ""
        star_client = getattr(agent, "_star_client", None)
        if star_client:
            try:
                # 重新加载会话状态（add_turn 可能已更新 version）
                state = await session_manager.get_session(session_id)

                history = []
                try:
                    turns = await session_manager.get_history(session_id, limit=20)
                    history = [{"speaker": t.speaker, "content": t.content} for t in turns]
                except Exception:
                    logger.debug("加载对话历史失败: session=%s", session_id)

                # 转接摘要 = 对话摘要(如有) + 最后回复
                conversation_summary = state.conversation_summary if state else ""
                transfer_summary = f"{conversation_summary}\n\n[最近回复] {reply}" if conversation_summary else reply

                # 已知实体随转接传递
                known_entities = []
                if state and state.last_entities:
                    known_entities = [{"type": e.entity_type, "value": e.value} for e in state.last_entities]

                transfer_req = star_client.build_transfer_request(
                    session_id=session_id,
                    transfer_reason=transfer_reason,
                    transfer_summary=transfer_summary,
                    history=history,
                    intent=str(primary_intent.value) if primary_intent and hasattr(primary_intent, "value") else "",
                    sentiment=str(result.get("sentiment", "neutral")),
                )
                transfer_resp = await star_client.create_session(transfer_req)

                # 将转人工摘要 + 实体写入 session 状态，供坐席端 assist_ready 读取
                with contextlib.suppress(Exception):
                    state.transfer_summary = transfer_summary
                    state.transfer_reason = transfer_reason
                    await session_manager._save_meta(state)
                transfer_url = transfer_resp.get("pollUrl", "") or transfer_resp.get("poll_url", "")
                if transfer_url:
                    logger.info(
                        "转人工已创建: bot=%s star=%s",
                        session_id,
                        transfer_resp.get("sessionId", transfer_resp.get("session_id", "")),
                    )
            except Exception:
                logger.exception("转人工调用 star-connection 失败")
                transfer_reason += "（人工客服系统暂不可用）"
        else:
            logger.warning("star_client 未初始化，跳过转人工桥接: session=%s", session_id)

        await _finish_message(
            redis_client,
            session_id,
            reply,
            intent=str(primary_intent.value) if primary_intent else None,
            confidence=primary_confidence,
            source=source,
        )
        # 审计更新：转人工完成
        if _db_session_factory and orig_message_id:
            await update_chat_message(
                _db_session_factory,
                orig_message_id,
                processing_status=ChatMessageStatus.DONE,
                intent=str(primary_intent.value) if primary_intent else None,
                source=source,
            )
            # 合并消息的审计更新
            if merged_message_ids:
                for merged_id in merged_message_ids:
                    await update_chat_message(
                        _db_session_factory,
                        merged_id,
                        processing_status=ChatMessageStatus.DONE,
                        intent=str(primary_intent.value) if primary_intent else None,
                        source="merged",
                    )
        # 额外写 transfer 信息到 response key 的扩展字段
        response_key = f"{RESPONSE_KEY_PREFIX}:{session_id}"
        existing = await redis_client.get(response_key)
        if existing:
            data = json.loads(existing)
            data["is_transfer"] = True
            data["transfer_url"] = transfer_url
            data["transfer_reason"] = transfer_reason
            await redis_client.setex(response_key, RESPONSE_TTL, json.dumps(data, ensure_ascii=False))
        await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
        return

    # 非转人工: 写 response + 通知
    await _finish_message(
        redis_client,
        session_id,
        reply,
        intent=str(primary_intent.value) if primary_intent else None,
        confidence=primary_confidence,
        source=source,
    )
    # 审计更新：处理完成
    if _db_session_factory and orig_message_id:
        await update_chat_message(
            _db_session_factory,
            orig_message_id,
            processing_status=ChatMessageStatus.DONE,
            intent=str(primary_intent.value) if primary_intent else None,
            source=source,
        )
        # 合并消息的审计更新
        if merged_message_ids:
            for merged_id in merged_message_ids:
                await update_chat_message(
                    _db_session_factory,
                    merged_id,
                    processing_status=ChatMessageStatus.DONE,
                    intent=str(primary_intent.value) if primary_intent else None,
                    source="merged",
                )
    await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
    # 清理重试计数（如果消息曾被 XAUTOCLAIM 认领过）
    with contextlib.suppress(Exception):
        await redis_client.hdel("smartcs:chat:retry_count", str(msg_id))

    logger.info(
        "消息处理完成: session=%s intent=%s source=%s merged=%d",
        session_id,
        primary_intent.value if primary_intent and hasattr(primary_intent, "value") else "unknown",
        source,
        len(merged_message_ids) if merged_message_ids else 0,
    )


# ── Consumer 主循环 ─────────────────────────────────────────────


async def _consumer_loop(redis_client, agent) -> None:
    """消息消费主循环: XREADGROUP 批量消费 → create_task 异步并行"""
    consumer_name = f"bot-worker-{os.getpid()}"
    logger.info("消息消费循环已启动: consumer=%s", consumer_name)

    try:
        while True:
            try:
                result = await redis_client.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=consumer_name,
                    streams={CHAT_STREAM_KEY: ">"},
                    count=10,
                    block=1000,
                )
            except Exception:
                logger.exception("XREADGROUP 异常, 1s 后重试")
                await asyncio.sleep(1)
                continue

            if not result:
                continue

            for _stream_name, messages in result:
                for msg_id, fields in messages:
                    fields["_enqueue_time"] = asyncio.get_event_loop().time()
                    asyncio.create_task(_dispatch_message(redis_client, agent, msg_id, fields))

    except asyncio.CancelledError:
        logger.info("消息消费循环收到取消信号")
        raise


# ── 监控指标 ────────────────────────────────────────────────────

# 运行时指标（供 health check 和 Prometheus 读取）
_metrics: dict = {
    "p": 0,  # PEL pending count
    "sl": 0,  # Stream length
    "as": 0,  # Active session workers
    "su": 0.0,  # Semaphore utilization (0.0-1.0)
    "fr": 0,  # Fast reply count (累计)
    "to": 0,  # Timeout/skip count (累计)
    "mg": 0,  # Merge count (调用前队列检查合并的消息数, 累计)
}


async def _monitoring_loop(redis_client) -> None:
    """后台监控协程: 每 15s 采集一次指标"""
    while True:
        try:
            await asyncio.sleep(15)

            # PEL 待处理消息数
            try:
                pending = await redis_client.xpending(CHAT_STREAM_KEY, CONSUMER_GROUP)
                _metrics["p"] = pending.get("pending", 0) if isinstance(pending, dict) else len(pending)
            except Exception:
                pass

            # Stream 总长度
            try:
                _metrics["sl"] = await redis_client.xlen(CHAT_STREAM_KEY)
            except Exception:
                pass

            # 活跃 session worker 数
            _metrics["as"] = len(_session_active)

            # Semaphore 利用率
            if _agent_semaphore:
                total = getattr(get_settings(), "bot", None)
                max_slots = total.max_concurrent_agents if total else 10
                _metrics["su"] = round(1.0 - (_agent_semaphore._value / max_slots), 2)
            else:
                _metrics["su"] = 0.0

            logger.debug(
                "Bot 指标: pel=%d stream_len=%d active_workers=%d sem_util=%.2f fast_reply=%d timeout=%d",
                _metrics["p"],
                _metrics["sl"],
                _metrics["as"],
                _metrics["su"],
                _metrics["fr"],
                _metrics["to"],
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("监控采集异常")


# ── Lifespan helpers ────────────────────────────────────────────


async def start_bot_worker(app) -> None:
    """启动 Bot 后台协程 (在 lifespan 中调用)"""
    global _agent_semaphore, _db_session_factory, _rule_loader
    settings = get_settings()
    _agent_semaphore = asyncio.Semaphore(settings.bot.max_concurrent_agents)
    _db_session_factory = getattr(app.state, "db_session_factory", None)

    # 加载 L1 规则（DB → 内存，DB 不可用则 fallback 到内存种子规则）
    if _db_session_factory:
        await _rule_loader.load_from_db(_db_session_factory)
    if not _rule_loader.rules:
        _rule_loader.load_from_memory()

    redis_client = app.state.redis_client
    agent = app.state.agent  # SmartCSAgent 实例, 由 init_agent 注入

    # 启动规则热加载监听
    if _db_session_factory:
        await _rule_loader.start_hot_reload(redis_client, _db_session_factory)

    # 加载安全过滤敏感词
    from smartcs.shared.safety import safety_filter

    safety_filter.load_from_file("config/sensitive_words.txt")

    # 确保 Stream 和 consumer group 存在
    await _init_stream_group(redis_client)

    # 启动消息消费循环
    consumer_task = asyncio.create_task(_consumer_loop(redis_client, agent))
    app.state._consumer_task = consumer_task

    # 启动 XAUTOCLAIM 兜底协程
    claim_task = asyncio.create_task(_claim_stale(redis_client, agent))
    app.state._claim_task = claim_task

    # 启动监控采集
    monitor_task = asyncio.create_task(_monitoring_loop(redis_client))
    app.state._monitor_task = monitor_task

    logger.info("Bot 后台工作器已启动 (Streams + Consumer Group + XAUTOCLAIM + Monitoring)")


async def stop_bot_worker(app) -> None:
    """停止 Bot 后台协程 (在 lifespan 中调用)"""
    for attr in ("_consumer_task", "_claim_task", "_monitor_task"):
        task = getattr(app.state, attr, None)
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    logger.info("Bot 后台工作器已停止")


# ── HTTP Endpoints ──────────────────────────────────────────────


@router.get("/health")
async def health_check(req: Request):
    """机器人服务健康检查（含依赖状态 + 运行时指标）"""
    from smartcs.shared.health import aggregate_health, check_all_dependencies

    deps = await check_all_dependencies(req.app)
    overall, http_code = aggregate_health(deps)

    settings = get_settings()
    max_slots = settings.bot.max_concurrent_agents
    slots_available = _agent_semaphore._value if _agent_semaphore else max_slots

    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=http_code,
        content={
            "status": overall,
            "service": "bot",
            "dependencies": deps,
            "agent_slots": {
                "total": max_slots,
                "available": slots_available,
            },
            "streams": {
                "pending": _metrics["p"],
                "stream_length": _metrics["sl"],
                "active_workers": _metrics["as"],
                "semaphore_utilization": _metrics["su"],
            },
            "stats": {
                "fast_reply_total": _metrics["fr"],
                "timeout_total": _metrics["to"],
                "merge_total": _metrics["mg"],
            },
            "rules": {
                "l1_rule_count": len(_rule_loader.rules),
            },
        },
    )


@router.get("/health/live")
async def health_live():
    """Liveness 探针：进程存活即 200"""
    return {"status": "alive"}


@router.get("/health/ready")
async def health_ready(req: Request):
    """Readiness 探针：检查核心依赖连通性"""
    from fastapi.responses import JSONResponse

    from smartcs.shared.health import aggregate_health, check_all_dependencies

    deps = await check_all_dependencies(req.app)
    overall, http_code = aggregate_health(deps)
    return JSONResponse(
        status_code=http_code,
        content={"status": overall, "dependencies": deps},
    )


@router.post("/chat/send", response_model=ChatSendResponse)
async def chat_send(body: ChatSendRequest, req: Request):
    """客户端发送消息接口

    消息写入 Redis Stream 持久化, 由后台 consumer 异步处理。
    客户端通过 GET /api/chat/poll 订阅通知获取结果。
    """
    redis_client = getattr(req.app.state, "redis_client", None)
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis 未就绪")

    # 输入校验: 拒绝空消息 (含全角空格/零宽字符)
    msg = (
        (body.message or "").replace("　", " ").replace("​", "").replace("‌", "").replace("‍", "").replace("﻿", "").strip()
    )
    if not msg:
        raise HTTPException(status_code=422, detail="消息内容不能为空")

    # 安全过滤：检测客户输入中的敏感词
    from smartcs.shared.safety import safety_filter

    is_safe, hit_words = safety_filter.check_input(msg)
    if not is_safe:
        logger.warning("客户输入包含敏感词: session=%s hits=%s", body.session_id, hit_words)
        # 敏感词不拦截，但记录审计（银行场景需要知道客户提到了什么敏感话题）

    session_id = body.session_id or uuid_module.uuid4().hex
    message_id = uuid_module.uuid4().hex

    # 注入 trace context 到 Stream 消息 (全链路串联)
    trace_ctx = ""
    try:
        from opentelemetry import trace as otel_trace

        span_ctx = otel_trace.get_current_span().get_span_context()
        if span_ctx.is_valid:
            trace_ctx = f"{span_ctx.trace_id:032x}:{span_ctx.span_id:016x}:{span_ctx.trace_flags:02x}"
    except Exception:
        pass

    # 写入 Stream (XADD 持久化, MAXLEN 防止无限增长)
    await redis_client.xadd(
        CHAT_STREAM_KEY,
        {
            "session_id": session_id,
            "message_id": message_id,
            "message": body.message,
            "_trace_context": trace_ctx,
            "customer_id": body.customer_id or "",
            "channel": body.channel.value if body.channel else "web",
        },
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )

    return ChatSendResponse(
        accepted=True,
        message_id=message_id,
        session_id=session_id,
    )


@router.get("/chat/poll")
async def chat_poll(
    req: Request,
    session_id: str = Query(...),
    timeout: int = Query(default=25, ge=1, le=60),
):
    """长轮询获取机器人回复

    通过 Redis Pub/Sub 监听完成通知, worker 完成即刻唤醒。
    返回不同状态: queued → processing → done / timeout
    """
    redis_client = getattr(req.app.state, "redis_client", None)
    if redis_client is None:
        return JSONResponse(content=_build_poll_json(status="timeout", suggestion="请稍后重试"))

    response_key = f"{RESPONSE_KEY_PREFIX}:{session_id}"
    notify_channel = f"{NOTIFY_CHANNEL_PREFIX}:{session_id}"

    # 1. 快速路径: 结果已就绪
    raw = await redis_client.get(response_key)
    if raw:
        await redis_client.delete(response_key)
        data = json.loads(raw)
        return Response(
            content=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            media_type="application/json",
        )

    # 2. 订阅 Pub/Sub 等待通知 (带超时保护)
    try:
        result = await asyncio.wait_for(
            _wait_for_response(redis_client, response_key, notify_channel, timeout),
            timeout=timeout + 2,  # 硬超时 = 用户超时 + 2s 缓冲
        )
        return result
    except TimeoutError:
        return JSONResponse(
            content=_build_poll_json(
                status="timeout",
                suggestion="请稍后重试或输入'转人工'",
            )
        )


async def _wait_for_response(
    redis_client,
    response_key: str,
    notify_channel: str,
    timeout: int,
) -> Response:
    """等待 Pub/Sub 通知或超时, 返回 response 或 timeout JSON"""
    pubsub = redis_client.pubsub()
    if asyncio.iscoroutine(pubsub):
        pubsub = await pubsub
    await pubsub.subscribe(notify_channel)

    try:
        start_time = asyncio.get_event_loop().time()
        listener = pubsub.listen()
        # 兼容 mock 环境: pubsub.listen() 可能返回协程而非异步迭代器
        if asyncio.iscoroutine(listener):
            listener = await listener
        try:
            async for msg in listener:
                elapsed = asyncio.get_event_loop().time() - start_time

                if elapsed > timeout:
                    raw = await redis_client.get(response_key)
                    if raw:
                        await redis_client.delete(response_key)
                        data = json.loads(raw)
                        return Response(
                            content=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                            media_type="application/json",
                        )
                    return JSONResponse(
                        content=_build_poll_json(
                            status="timeout",
                            suggestion="请稍后重试或输入'转人工'",
                        )
                    )

                if msg["type"] == "subscribe":
                    raw = await redis_client.get(response_key)
                    if raw:
                        await pubsub.unsubscribe(notify_channel)
                        await redis_client.delete(response_key)
                        data = json.loads(raw)
                        return Response(
                            content=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                            media_type="application/json",
                        )

                elif msg["type"] == "message":
                    raw = await redis_client.get(response_key)
                    if raw:
                        await redis_client.delete(response_key)
                        data = json.loads(raw)
                        return Response(
                            content=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                            media_type="application/json",
                        )
                    # 短暂等待 response key 就绪
                    await asyncio.sleep(0.05)
                    raw = await redis_client.get(response_key)
                    if raw:
                        await redis_client.delete(response_key)
                        data = json.loads(raw)
                        return Response(
                            content=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                            media_type="application/json",
                        )
        except (TypeError, AttributeError):
            # listener 不支持异步迭代（mock 或异常环境），降级为超时
            pass

        # 迭代器耗尽或异常，返回超时
        return JSONResponse(
            content=_build_poll_json(
                status="timeout",
                suggestion="请稍后重试或输入'转人工'",
            )
        )

    finally:
        await pubsub.unsubscribe(notify_channel)


@router.post("/kb/retrieve", response_model=RetrieveResponse)
async def kb_retrieve(
    request: RetrieveRequest,
    es_client: ESClientDep,
    milvus_collection: MilvusCollectionDep,
    embedding_breaker: EmbeddingBreakerDep,
    reranker: RerankerProviderDep,
    es_breaker: ESBreakerDep,
    milvus_breaker: MilvusBreakerDep,
):
    """知识库检索接口

    支持混合检索（BM25 + 向量 + RRF 融合）、BM25 单路、向量单路三种模式，
    可选 Reranker 精排。自动降级：嵌入服务不可用时降级到 BM25 only。
    ES/Milvus 熔断器打开时跳过对应检索路。
    """
    embedding_provider = embedding_breaker.provider if embedding_breaker.is_available else None

    # 熔断器检查：打开时跳过对应检索路
    effective_es = es_client if (es_client and es_breaker and es_breaker.allow_request()) else None
    effective_milvus = (
        milvus_collection if (milvus_collection and milvus_breaker and milvus_breaker.allow_request()) else None
    )

    try:
        result = await retrieve(
            request=request,
            es_client=effective_es,
            milvus_collection=effective_milvus,
            embedding_provider=embedding_provider,
            reranker=reranker,
        )
        # 记录成功
        if es_breaker and effective_es:
            es_breaker.record_success()
        if milvus_breaker and effective_milvus:
            milvus_breaker.record_success()
        return result
    except Exception:
        # 记录失败
        if es_breaker and effective_es:
            es_breaker.record_failure()
        if milvus_breaker and effective_milvus:
            milvus_breaker.record_failure()
        raise


@router.post("/kb/documents")
async def upload_document(
    db: DbSession,
    es_client: ESClientDep,
    milvus_collection: MilvusCollectionDep,
    minio_client: MinioClientDep,
    embedding_breaker: EmbeddingBreakerDep,
    file: UploadFile = File(...),  # noqa: B008
    category: str = Form(...),
    doc_type: str = Form(...),
    card_type: str | None = Form(None),
    customer_tier: str | None = Form(None),
    security_level: str = Form("internal"),
    version: str = Form("1.0"),
    effective_date: str | None = Form(None),
    expiry_date: str | None = Form(None),
    keywords: str = Form(""),
):
    """文档上传接口

    上传文件到 MinIO，创建知识库文档记录，触发摄入管线。
    支持格式：PDF, DOCX, HTML, MD, TXT, XLSX。
    """
    # 1. 校验文件扩展名
    filename = file.filename or ""
    suffix = ""
    if "." in filename:
        suffix = "." + filename.rsplit(".", 1)[-1].lower()

    if suffix not in _ALLOWED_EXTENSIONS:
        raise DocumentFormatError(f"不支持的文件格式: {suffix}，支持: {', '.join(sorted(_ALLOWED_EXTENSIONS))}")

    source_type_str = _EXT_TO_SOURCE[suffix]

    # 2. 读取文件内容
    content_bytes = await file.read()
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    file_size = len(content_bytes)

    # 3. 上传到 MinIO
    minio_object_key = f"{category}/{filename}"
    if minio_client:
        from io import BytesIO

        settings = get_settings()
        bucket_name = settings.minio.bucket
        await asyncio.to_thread(
            minio_client.put_object,
            bucket_name,
            minio_object_key,
            BytesIO(content_bytes),
            file_size,
            content_type=file.content_type or "application/octet-stream",
        )

    # 4. 创建 KbDocument 记录
    from datetime import date as date_type

    kb_doc = KbDocument(
        title=filename,
        source_type=KbSourceType[source_type_str],
        file_path=minio_object_key,
        file_size=file_size,
        content_hash=content_hash,
        category=category,
        doc_type=doc_type,
        card_type=card_type,
        customer_tier=customer_tier,
        security_level=security_level,
        version=version,
        effective_date=date_type.fromisoformat(effective_date) if effective_date else None,
        expiry_date=date_type.fromisoformat(expiry_date) if expiry_date else None,
        status=KbDocStatus.PENDING,
        created_by="api_upload",
    )
    db.add(kb_doc)
    await db.flush()

    # 5. 触发摄入管线
    from smartcs.services.common.ingestion import ingest_document
    from smartcs.shared.models import DocumentMetadata

    doc_metadata = DocumentMetadata(
        doc_id=str(kb_doc.id),
        category=category,
        doc_type=doc_type,
        keywords=[k.strip() for k in keywords.split(",") if k.strip()] if keywords else [],
        card_type=card_type,
        customer_tier=customer_tier,
        effective_date=effective_date,
        expiry_date=expiry_date,
        security_level=security_level,
        version=version,
    )

    embedding_provider = embedding_breaker.provider if embedding_breaker.is_available else None

    if source_type_str in ("MARKDOWN", "TXT", "HTML"):
        text_content = content_bytes.decode("utf-8")
    else:
        text_content = minio_object_key

    try:
        final_status = await ingest_document(
            doc_id=kb_doc.id,
            file_path=text_content,
            source_type=KbSourceType[source_type_str],
            metadata=doc_metadata,
            embedding_provider=embedding_provider,
            db_session=db,
            es_client=es_client,
            milvus_collection=milvus_collection,
        )
        kb_doc.status = KbDocStatus.COMPLETED if final_status == "COMPLETED" else KbDocStatus.FAILED
    except Exception:
        kb_doc.status = KbDocStatus.FAILED

    await db.flush()

    return {
        "doc_id": str(kb_doc.id),
        "status": kb_doc.status.value,
        "chunk_count": kb_doc.chunk_count,
    }


# ── 会话管理 ──


class ChatEndRequest(BaseModel):
    session_id: str


@router.post("/chat/end")
async def chat_end(body: ChatEndRequest, req: Request):
    """客户主动结束会话"""
    session_manager = getattr(req.app.state, "session_manager", None)
    if session_manager:
        try:
            await session_manager.transition_phase(
                body.session_id,
                SessionPhase.ENDED,
                reason="customer_ended",
            )
        except Exception:
            pass  # 会话可能已结束
    return {"status": "ok", "session_id": body.session_id}


class ChatTransferRequest(BaseModel):
    session_id: str
    reason: str = "customer_request"


@router.post("/chat/transfer")
async def chat_transfer(body: ChatTransferRequest, req: Request):
    """客户主动请求转人工"""
    session_manager = getattr(req.app.state, "session_manager", None)
    if session_manager:
        try:
            await session_manager.transition_phase(
                body.session_id,
                SessionPhase.AGENT,
                new_sub_phase=SessionSubPhase.AG_QUEUED,
                reason=body.reason,
            )
        except Exception:
            pass

    # 通知 star-connection 创建转人工会话
    star_client = getattr(req.app.state, "star_client", None)
    transfer_url = ""
    if star_client:
        try:
            result = await star_client.create_session(body.session_id)
            transfer_url = result.get("transfer_url", "")
        except Exception:
            logger.warning("star-connection 转人工通知失败: session=%s", body.session_id)

    return {"status": "transferring", "session_id": body.session_id, "transfer_url": transfer_url}


# ── 客户反馈 ──


class ChatFeedbackRequest(BaseModel):
    session_id: str
    message_id: str
    rating: Literal["up", "down", ""] = ""
    comment: str = ""


@router.post("/chat/feedback")
async def chat_feedback(body: ChatFeedbackRequest, req: Request):
    """客户对 Bot 回复的反馈（点赞/踩/评论）"""
    redis_client = getattr(req.app.state, "redis_client", None)
    if redis_client:
        feedback_key = f"smartcs:feedback:customer:{body.message_id}"
        import json as _json

        await redis_client.setex(
            feedback_key,
            86400,
            _json.dumps(
                {
                    "session_id": body.session_id,
                    "message_id": body.message_id,
                    "rating": body.rating,
                    "comment": body.comment,
                },
                ensure_ascii=False,
            ),
        )
    return {"status": "ok"}


# ── 会话历史 ──


@router.get("/sessions")
async def list_sessions(
    req: Request,
    limit: int = 20,
    offset: int = 0,
):
    """列出会话（从 Redis 扫描活跃会话）"""
    redis_client = getattr(req.app.state, "redis_client", None)
    if not redis_client:
        return {"sessions": [], "total": 0}

    # 使用 SCAN 迭代（非阻塞），避免 KEYS 阻塞 Redis
    session_keys: list[str] = []
    async for key in redis_client.scan_iter(match="smartcs:session:*:meta", count=100):
        session_keys.append(key if isinstance(key, str) else key.decode())
    total = len(session_keys)
    sessions = []
    for key in session_keys[offset : offset + limit]:
        raw = await redis_client.get(key)
        if raw:
            try:
                meta = json.loads(raw)
                sessions.append(
                    {
                        "session_id": meta.get("session_id"),
                        "current_phase": meta.get("current_phase"),
                        "sub_phase": meta.get("sub_phase"),
                        "turn_count": meta.get("turn_count", 0),
                        "last_active_at": meta.get("last_active_at"),
                        "agent_id": meta.get("agent_id"),
                    }
                )
            except Exception:
                continue
    return {"sessions": sessions, "total": total}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, req: Request, limit: int = 50):
    """获取会话消息历史"""
    redis_client = getattr(req.app.state, "redis_client", None)
    if not redis_client:
        return {"messages": []}

    key = f"smartcs:session:{session_id}:history"
    raw_list = await redis_client.lrange(key, -limit, -1)
    messages = []
    for raw in raw_list:
        try:
            turn = json.loads(raw)
            messages.append(
                {
                    "speaker": turn.get("speaker"),
                    "content": turn.get("content"),
                    "timestamp": turn.get("timestamp"),
                    "intent": turn.get("intent"),
                }
            )
        except Exception:
            continue
    return {"session_id": session_id, "messages": messages, "count": len(messages)}


# ── KB 管理 ──


@router.get("/kb/documents")
async def list_documents(
    db: DbSession,
    category: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """列出知识库文档"""
    from sqlalchemy import func, select

    query = select(KbDocument).where(KbDocument.is_deleted == False)  # noqa: E712
    if category:
        query = query.where(KbDocument.category == category)
    if status:
        query = query.where(KbDocument.status == status)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(KbDocument.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    docs = result.scalars().all()

    return {
        "documents": [
            {
                "doc_id": str(d.id),
                "title": d.title,
                "source_type": d.source_type,
                "category": d.category,
                "doc_type": d.doc_type,
                "status": d.status,
                "chunk_count": d.chunk_count,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ],
        "total": total,
    }


@router.delete("/kb/documents/{doc_id}")
async def delete_document(doc_id: str, db: DbSession):
    """软删除知识库文档"""
    from sqlalchemy import select

    result = await db.execute(select(KbDocument).where(KbDocument.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        from smartcs.shared.exceptions import SmartCSError

        raise SmartCSError(code=2001, message=f"文档不存在: {doc_id}")

    doc.is_deleted = True
    doc.deleted_at = datetime.now()
    await db.flush()

    # TODO: 同步清理 ES 和 Milvus 中的索引

    return {"status": "ok", "doc_id": doc_id}


@router.get("/kb/documents/{doc_id}/status")
async def get_document_status(doc_id: str, db: DbSession):
    """查看文档摄入状态"""
    from sqlalchemy import select

    from smartcs.shared.orm_models import KbIngestionLog

    result = await db.execute(select(KbDocument).where(KbDocument.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        from smartcs.shared.exceptions import SmartCSError

        raise SmartCSError(code=2001, message=f"文档不存在: {doc_id}")

    logs_result = await db.execute(
        select(KbIngestionLog).where(KbIngestionLog.document_id == doc_id).order_by(KbIngestionLog.created_at)
    )
    logs = logs_result.scalars().all()

    return {
        "doc_id": doc_id,
        "title": doc.title,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "stages": [
            {
                "stage": log.stage,
                "status": log.status,
                "duration_ms": log.duration_ms,
                "error_message": log.error_message,
            }
            for log in logs
        ],
    }
