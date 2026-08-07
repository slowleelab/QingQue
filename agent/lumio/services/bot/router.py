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
import time
import uuid as uuid_module
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from lumio.services.common.audit import update_chat_message, write_chat_message
from lumio.services.common.deps import (
    DbSession,
    EmbeddingBreakerDep,
    ESBreakerDep,
    ESClientDep,
    MilvusBreakerDep,
    MilvusCollectionDep,
    MinioClientDep,
    RerankerProviderDep,
)
from lumio.services.common.retrieval import retrieve
from lumio.services.common.rule_loader import RuleLoader
from lumio.shared.auth import CurrentUser
from lumio.shared.config import get_settings
from lumio.shared.exceptions import DocumentFormatError
from lumio.shared.metrics import (
    BOT_ACTIVE_WORKERS,
    BOT_AGENT_RESPONSES,
    BOT_FAST_REPLY,
    BOT_SEMAPHORE_UTILIZATION,
    BOT_STREAM_LENGTH,
    BOT_STREAM_PENDING,
    DEAD_LETTER_WRITES,
)
from lumio.shared.models import (
    ChatSendRequest,
    ChatSendResponse,
    RetrieveRequest,
    RetrieveResponse,
    SessionPhase,
    SessionSubPhase,
)
from lumio.shared.orm_models import ChatMessageStatus, KbDocStatus, KbDocument, KbSourceType
from lumio.shared.rate_limit import get_limiter

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bot"])

# ── Redis 键 ────────────────────────────────────────────────────
CHAT_STREAM_KEY = "lumio:chat:stream"
DEAD_LETTER_KEY = "lumio:chat:dead_letter"
CONSUMER_GROUP = "bot-group"
RESPONSE_KEY_PREFIX = "lumio:response"
NOTIFY_CHANNEL_PREFIX = "lumio:notify"
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

# P2-18 修复: RuleLoader 返回的是 domain (card/bill/limit/...), 与 _FAST_REPLIES
# 键名 (lost_card/bill_query/...) 不一致 → 挂失/账单满荷时全掉 default 话术.
# 加 domain → 键别名映射对齐.
_DOMAIN_TO_FAST_KEY: dict[str, str] = {
    "card": "lost_card",
    "complaint": "complaint",
    "bill": "bill_query",
    "limit": "limit_query",
    "installment": "default",
    "reward": "default",
    "transfer": "default",
    "chitchat": "default",
    "default": "default",
}


def _quick_intent_match(message: str) -> str:
    """快速意图匹配 (Phase 3: RuleLoader DB+Redis 热加载, <5ms)

    P2-18: 结果经 domain 别名映射到 _FAST_REPLIES 键, 保证挂失/账单走专属话术.
    """
    intent, _ = _rule_loader.match(message)
    return _DOMAIN_TO_FAST_KEY.get(intent, "default")


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


def _ensure_session_owned(user: CurrentUser, session_id: str) -> None:
    """P0-1 第三轮修复: JWT 声明 session_id 存在时校验与请求一致 (越权防护).

    银行客服场景: 客户端首次 chat/send 时服务端生成 session_id 返回,
    客户端随后用该 session_id poll/end/transfer/feedback。
    若 JWT 中带 session_id (坐席/管理端场景), 必须与请求一致, 否则 403。
    普通客户 token 不带 session_id 声明时放行 (session_id 本身是随机会话 ID)。
    """
    from lumio.shared.auth import AuthorizationError

    if user.session_id and user.session_id != session_id:
        raise AuthorizationError("无权访问该会话")


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
    from lumio.shared.safety import safety_filter

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


# ── P1-2 第三轮修复: message 级幂等标记 ──
# 旧实现按 session 级 response_key 判断幂等: 消息 A 处理中用户连发 B,
# A 写完 response key 后 B 被误判"已处理"而 XACK 丢弃 (消息永久丢失, at-least-once 被破坏).
# 现改为 message_id 维度: 每条消息处理完成后标记, XAUTOCLAIM 重投递时跳过已处理的.
_PROCESSED_PREFIX = "lumio:processed"
_PROCESSED_TTL = 300  # 5 分钟, 覆盖 XAUTOCLAIM 60s 重投窗口


async def _mark_processed(redis_client, client_message_id: str) -> None:
    """标记消息已处理 (幂等键, 防 at-least-once 重投递重复执行)."""
    if not client_message_id:
        return
    with contextlib.suppress(Exception):
        await redis_client.setex(f"{_PROCESSED_PREFIX}:{client_message_id}", _PROCESSED_TTL, "1")


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
    retry_counter_key = "lumio:chat:retry_count"

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
                        # P0-5: 死信指标 + 告警日志
                        DEAD_LETTER_WRITES.labels(reason="retry_exhausted").inc()
                        logger.error(
                            "DEAD_LETTER: 消息重试超限进入死信 session=%s msg_id=%s — 需人工处理",
                            fields.get("session_id", ""),
                            msg_id,
                        )
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
    from lumio.shared.config import get_settings

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
                    await _mark_processed(redis_client, client_message_id)  # P1-2
                    continue

                # 2. 幂等检查 (P1-2: message 级维度, 防 XAUTOCLAIM 重投递重复执行)
                # 旧实现按 session 级 response_key 判断: 用户连发消息时新消息被误杀,
                # 且 return 直接杀死整个 per-session worker (队列残留消息滞留 60s).
                if client_message_id:
                    processed_key = f"{_PROCESSED_PREFIX}:{client_message_id}"
                    if await redis_client.get(processed_key):
                        logger.debug(
                            "消息幂等跳过(已处理): session=%s msg=%s",
                            session_id,
                            client_message_id,
                        )
                        await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
                        continue

                # 3. Semaphore 满荷 → 快速兜底 (P2-18: 接线 fast_reply_cooldown —
                # 冷却期内同一会话不做二次快速兜底, 避免连续给客户模板话术)
                cooldown_ok = True
                try:
                    cooldown_ts = await redis_client.get(f"lumio:fast_reply:{session_id}")
                    if cooldown_ts:
                        cooldown = get_settings().bot.fast_reply_cooldown
                        if time.time() - float(cooldown_ts) < cooldown:
                            cooldown_ok = False
                except Exception:
                    pass
                if _agent_semaphore and _agent_semaphore.locked() and cooldown_ok:
                    intent_hint = _quick_intent_match(message)
                    reply = _FAST_REPLIES.get(intent_hint, _FAST_REPLIES["default"])
                    await _finish_message(redis_client, session_id, reply, intent=intent_hint, source="fast_reply")
                    await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
                    _metrics["fr"] += 1
                    BOT_FAST_REPLY.inc()
                    # 记录冷却时间戳
                    with contextlib.suppress(Exception):
                        await redis_client.set(f"lumio:fast_reply:{session_id}", str(time.time()), ex=60)
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
                    await _mark_processed(redis_client, client_message_id)  # P1-2
                    return

                # 4. 标准 Agent 处理路径
                # 4a. 调用前队列检查: peek 队列并 drain 排队消息, 合并后一次 LLM 调用
                # P2 第三轮修复: 合并上限 (≤5 条 / ≤4000 字符) — 旧代码无界合并,
                # 攻击者短时灌入大量消息 → 数千字符进 LLM prompt
                max_merge_count = 5
                max_merge_chars = 4000
                merged_message_ids: list[str] = []
                merged_contents: list[str] = []

                if not q.empty():
                    while True:
                        try:
                            pending_msg_id, pending_fields = q.get_nowait()
                        except asyncio.QueueEmpty:
                            break

                        # 超限: 该条放回队列, 留给下一轮处理 (不丢失)
                        if len(merged_message_ids) >= max_merge_count or (
                            sum(len(m) for m in merged_contents) + len(pending_fields.get("message", ""))
                            > max_merge_chars
                        ):
                            q.put_nowait((pending_msg_id, pending_fields))
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
                    # FIX-5: 语义标记 — LLM 明确感知这是用户连续发送的多条消息,
                    # 避免把合并文本当成一句混乱的话 (旧实现直接 \n 拼接无说明)
                    merged_message = (
                        f"（用户连续发送了 {len(merged_contents) + 1} 条消息，请一次性综合回复）\n"
                        + message
                        + "\n"
                        + "\n".join(merged_contents)
                    )
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
                            customer_id=customer_id,
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
                        # P0-5: 死信指标 + 告警日志 (Grafana alert 依赖)
                        DEAD_LETTER_WRITES.labels(reason="agent_error").inc()
                        logger.error(
                            "DEAD_LETTER: 消息进入死信队列 session=%s msg_id=%s — 需人工处理",
                            fields.get("session_id", ""),
                            msg_id,
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
    customer_id: str = "",  # P1-6 第三轮修复: 透传 customer_id (画像学习依赖)
    merged_message_ids: list[str] | None = None,
) -> None:
    """标准 Agent 处理路径 (Semaphore 内)

    Args:
        merged_message_ids: 被合并的消息 ID 列表（调用前队列检查合并的消息）
    """
    session_manager = agent._session_manager

    # Session 获取或创建
    state = await session_manager.get_or_create(session_id)

    # ENDED 会话复活: 客户超时/主动结束后再次发消息 → 回 BOT_ACTIVE 续聊
    # (保留历史/画像等 Redis 数据, 守卫由 transition_phase 重新启动)
    if state.current_phase.value == "ended":
        logger.info("会话已 ENDED, 复活为 BOT_ACTIVE: session=%s reason=%s", session_id, state.end_reason)
        try:
            await session_manager.transition_phase(
                session_id,
                SessionPhase.BOT,
                new_sub_phase=SessionSubPhase.BOT_ACTIVE,
                reason="customer_returned",
            )
        except Exception as exc:
            logger.warning("会话复活失败, 降级按新会话处理: session=%s err=%s", session_id, exc)

    # 如果已进入 AGENT 阶段，跳过 bot 处理
    if state.current_phase.value == "agent":
        # P2 第三轮修复: 不再静默 XACK 丢弃 — 客户排队期间的消息需要可见反馈
        logger.info("会话已进入 AGENT 阶段, 跳过 bot: session=%s", session_id)
        # FIX-4: 消息真实写入 Redis 历史 (此前"已记录"话术与事实不符),
        # 供坐席摘要 / 转回 Bot 时保留上下文
        try:
            from lumio.shared.models import DialogueTurn

            turn = DialogueTurn(
                turn_id=uuid_module.uuid4().hex,
                session_id=session_id,
                speaker="customer",
                content=message,
                intent=None,
                confidence=0.0,
                entities=[],
            )
            await session_manager.add_turn(session_id, turn)
        except Exception as exc:
            logger.warning("AGENT 阶段消息写入历史失败: session=%s err=%s", session_id, exc)
        await _finish_message(
            redis_client,
            session_id,
            "您已转接人工客服, 排队期间的消息已为您记录, 客服将尽快回复。",
            source="transfer_queued",
        )
        await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
        return

    # Agent 编排 (P1-3 第三轮修复: 端到端 deadline, 防止 5 轮工具循环 × 60s 撑爆 semaphore)
    try:
        result = await asyncio.wait_for(
            agent.run(session_id, message, customer_id=customer_id or None),
            timeout=get_settings().orchestration.global_timeout_ms / 1000,
        )
    except TimeoutError:
        logger.warning("Agent 编排超时: session=%s (>%dms)", session_id, get_settings().orchestration.global_timeout_ms)
        await _finish_message(
            redis_client,
            session_id,
            "当前咨询量较大，回复超时，请重新发送或输入'转人工'。",
            source="timeout",
        )
        await redis_client.xack(CHAT_STREAM_KEY, CONSUMER_GROUP, msg_id)
        await _mark_processed(redis_client, orig_message_id)  # P1-2
        return

    intent = result.get("intent")
    primary_intent = intent.primary_intent if intent and hasattr(intent, "primary_intent") else None
    primary_confidence = intent.primary_confidence if intent and hasattr(intent, "primary_confidence") else 0.0
    is_transfer = result.get("should_transfer", False)
    transfer_reason = result.get("transfer_reason", "")
    reply = result.get("response", "抱歉，我暂时无法处理您的请求。")
    source = result.get("response_source", "fallback")
    entities = result.get("entities", [])
    BOT_AGENT_RESPONSES.labels(source=source).inc()

    # ── 保存对话历史（客户消息 + Bot 回复）──
    from uuid import uuid4 as _uuid4

    from lumio.shared.models import DialogueTurn

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
        chat_client = getattr(agent, "_chat_client", None)
        if chat_client:
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

                # 已知实体随转接传递 (P2 第三轮修复: 旧代码计算后未赋值, 实体列表被丢弃)
                known_entities: list[dict[str, str]] = []
                if state and state.last_entities:
                    known_entities = [{"type": e.entity_type, "value": e.value} for e in state.last_entities]

                transfer_req = chat_client.build_transfer_request(
                    session_id=session_id,
                    transfer_reason=transfer_reason,
                    transfer_summary=transfer_summary,
                    history=history,
                    intent=str(primary_intent.value) if primary_intent and hasattr(primary_intent, "value") else "",
                    sentiment=str(result.get("sentiment", "neutral")),
                    entities=known_entities,  # P2: 实体列表真正传入转接
                )
                transfer_resp = await chat_client.create_session(transfer_req)

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
                logger.exception("转人工调用 chat-svc 失败")
                transfer_reason += "（人工客服系统暂不可用）"
        else:
            logger.warning("chat_client 未初始化，跳过转人工桥接: session=%s", session_id)

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
        await _mark_processed(redis_client, orig_message_id)  # P1-2
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
        await redis_client.hdel("lumio:chat:retry_count", str(msg_id))
    await _mark_processed(redis_client, orig_message_id)  # P1-2 幂等标记

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
            with contextlib.suppress(Exception):
                _metrics["sl"] = await redis_client.xlen(CHAT_STREAM_KEY)

            # 活跃 session worker 数
            _metrics["as"] = len(_session_active)

            # Semaphore 利用率
            if _agent_semaphore:
                total = getattr(get_settings(), "bot", None)
                max_slots = total.max_concurrent_agents if total else 10
                _metrics["su"] = round(1.0 - (_agent_semaphore._value / max_slots), 2)
            else:
                _metrics["su"] = 0.0

            # 同步至 Prometheus 运行时 Gauge（与 /health 快照同源）
            BOT_STREAM_PENDING.set(_metrics["p"])
            BOT_STREAM_LENGTH.set(_metrics["sl"])
            BOT_ACTIVE_WORKERS.set(_metrics["as"])
            BOT_SEMAPHORE_UTILIZATION.set(_metrics["su"])

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
    agent = app.state.agent  # LumioAgent 实例, 由 init_agent 注入

    # 启动规则热加载监听
    if _db_session_factory:
        await _rule_loader.start_hot_reload(redis_client, _db_session_factory)

    # 加载安全过滤敏感词 (S6 第五轮修复: 用代码定位代替相对路径 — 旧实现依赖 CWD,
    # 进程 CWD 不同时文件找不到 → 过滤被静默禁用 (check_input 恒安全))
    from pathlib import Path as _Path

    from lumio.shared.safety import safety_filter

    _words_path = _Path(__file__).resolve().parents[3] / "config" / "sensitive_words.txt"
    if not _words_path.exists():
        _words_path = _Path(__file__).resolve().parents[2] / "config" / "sensitive_words.txt"
    loaded = safety_filter.load_from_file(str(_words_path))
    if loaded == 0:
        logger.warning("敏感词加载为 0 个: %s (内容安全过滤可能未生效)", _words_path)

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
    from lumio.shared.health import aggregate_health, check_all_dependencies

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
@get_limiter().exempt  # P2-3: 健康探针豁免限流 (流量高峰误 429 → 误判实例不可用)
async def health_live():
    """Liveness 探针：进程存活即 200"""
    return {"status": "alive"}


@router.get("/health/ready")
@get_limiter().exempt  # P2-3
async def health_ready(req: Request):
    """Readiness 探针：检查核心依赖连通性"""
    from fastapi.responses import JSONResponse

    from lumio.shared.health import aggregate_health, check_all_dependencies

    deps = await check_all_dependencies(req.app)
    overall, http_code = aggregate_health(deps)
    return JSONResponse(
        status_code=http_code,
        content={"status": overall, "dependencies": deps},
    )


@router.post("/chat/send", response_model=ChatSendResponse)
# P2-3 第五轮修复: 差异化限流 (rate_limit_chat=30/min, 此前死配置) —
# 用户级 key 防 NAT 共享配额; 登录接口类的高频滥用通道单独收紧
@get_limiter().limit("30/minute")
async def chat_send(body: ChatSendRequest, request: Request, user: CurrentUser):
    """客户端发送消息接口

    消息写入 Redis Stream 持久化, 由后台 consumer 异步处理。
    客户端通过 GET /api/chat/poll 订阅通知获取结果。
    """
    # P0-1 第三轮修复: chat/session 端点强制认证 + JWT 声明 session_id 归属校验
    _ensure_session_owned(user, body.session_id)
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        # P3-4 整改: 走统一错误体 (ServiceOverloadedError → 503)
        from lumio.shared.exceptions import ServiceOverloadedError

        raise ServiceOverloadedError("Redis 未就绪, 无法接收消息")

    # 输入校验: 拒绝空消息 (含全角空格/零宽字符)
    msg = (
        (body.message or "").replace("　", " ").replace("", "").replace("‌", "").replace("‍", "").replace("﻿", "").strip()
    )
    if not msg:
        # P3-4 整改: 走统一错误体 (IntentUnrecognizedError → 400)
        from lumio.shared.exceptions import IntentUnrecognizedError

        raise IntentUnrecognizedError("消息内容不能为空")

    # P0-6 第三轮修复: A3 注入防护接入生产入口 (此前 check_user_input 仅测试引用)
    # 命中已知注入模式 (忽略指令/角色混淆) → 拒绝请求, 不进 agent 链路
    from lumio.shared.injection_guard import InjectionAction, get_guard

    injection_verdict = get_guard().check_user_input(msg)
    if injection_verdict.action in (InjectionAction.REJECT, InjectionAction.QUARANTINE):
        logger.warning(
            "注入拦截 (入口): session=%s pattern=%s",
            body.session_id or "-",
            injection_verdict.pattern,
        )
        raise IntentUnrecognizedError("消息内容不符合规范, 请重新描述您的需求")

    # 安全过滤：检测客户输入中的敏感词
    from lumio.shared.safety import safety_filter

    is_safe, hit_words = safety_filter.check_input(msg)
    if not is_safe:
        logger.warning("客户输入包含敏感词: session=%s hits=%s", body.session_id, hit_words)
        # 敏感词不拦截，但记录审计（银行场景需要知道客户提到了什么敏感话题）

    session_id = body.session_id or uuid_module.uuid4().hex

    # P2-16: per-customer 会话上限 — 同客户多设备/多标签页无限开会话会耗尽
    # Redis state + 守卫资源. 用 ZSET 记录客户活跃会话 (score=最后活跃时间),
    # 超过上限且新会话未建时拒绝 (已带 session_id 的续聊不受影响).
    if body.customer_id and not body.session_id:
        try:
            customer_sessions_key = f"lumio:customer_sessions:{body.customer_id}"
            now_ts = time.time()
            # 清理 24h 前的过期记录
            await redis_client.zremrangebyscore(customer_sessions_key, 0, now_ts - 24 * 3600)
            active = await redis_client.zcard(customer_sessions_key)
            max_sessions = get_settings().bot.max_sessions_per_customer
            if active >= max_sessions:
                logger.warning(
                    "per-customer 会话超限: customer=%s active=%d limit=%d",
                    body.customer_id,
                    active,
                    max_sessions,
                )
                from lumio.shared.exceptions import BusinessError

                raise BusinessError(f"同时进行的会话数量已达上限 ({max_sessions})，请先结束其他会话")
        except Exception as exc:
            if isinstance(exc, BusinessError):
                raise
            logger.debug("per-customer 会话检查跳过 (非阻断): err=%s", exc)

    # FIX-7: 客户端幂等 — 同一 client_message_id 重复提交直接返回已接受, 不进 Stream
    # (双击/网络重试场景: 第二次提交不重复执行 agent, 幂等键由消费侧 _mark_processed 维护)
    client_idem_key = f"lumio:processed:{body.client_message_id}" if body.client_message_id else None
    if client_idem_key:
        try:
            if await redis_client.get(client_idem_key):
                logger.info(
                    "客户端幂等命中, 跳过重复提交: session=%s client_message_id=%s", session_id, body.client_message_id
                )
                return ChatSendResponse(accepted=True, message_id=body.client_message_id, session_id=session_id)
        except Exception:
            pass  # 幂等检查失败时放行 (at-least-once 语义)

    message_id = body.client_message_id or uuid_module.uuid4().hex

    # P2-16: 记录客户 → 会话 (ZADD, 新会话建立时)
    if body.customer_id and not body.session_id:
        with contextlib.suppress(Exception):
            await redis_client.zadd(
                f"lumio:customer_sessions:{body.customer_id}",
                {session_id: time.time()},
            )

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
    user: CurrentUser,
    session_id: str = Query(...),
    timeout: int = Query(default=25, ge=1, le=60),
):
    """长轮询获取机器人回复

    通过 Redis Pub/Sub 监听完成通知, worker 完成即刻唤醒。
    返回不同状态: queued → processing → done / timeout
    """
    # P0-1 第三轮修复: 强制认证 + session 归属校验
    _ensure_session_owned(user, session_id)
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
        # P2 第三轮修复: pubsub 连接必须 aclose 释放回池 —
        # 旧代码只 unsubscribe 不 aclose, 60/min/IP 轮询压力下耗尽 20 连接池
        with contextlib.suppress(Exception):
            await pubsub.aclose()


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
    user: CurrentUser,
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

    # 2. P3-7 整改: 文件大小上限 50MB (防 OOM 攻击)
    # Content-Length header 优先 (无须读 body), 缺失则 spools 50MB 上限给 fastapi 自动拒绝
    content_length = file.size if hasattr(file, "size") else None
    max_upload_size = 50 * 1024 * 1024
    if content_length is not None and content_length > max_upload_size:
        raise DocumentFormatError(f"文件过大: {content_length / 1024 / 1024:.1f}MB > 50MB 上限")

    # 3. 读取文件内容
    content_bytes = await file.read()
    file_size = len(content_bytes)
    if file_size > max_upload_size:
        raise DocumentFormatError(f"文件过大: {file_size / 1024 / 1024:.1f}MB > 50MB 上限")
    content_hash = hashlib.sha256(content_bytes).hexdigest()

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
        created_by=user.user_id,
    )
    db.add(kb_doc)
    await db.flush()

    # 5. 触发摄入管线
    from lumio.services.common.ingestion import ingest_document
    from lumio.shared.models import DocumentMetadata

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

    text_content = content_bytes.decode("utf-8") if source_type_str in ("MARKDOWN", "TXT", "HTML") else minio_object_key

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
async def chat_end(body: ChatEndRequest, req: Request, user: CurrentUser):
    """客户主动结束会话"""
    # P0-1 第三轮修复: 强制认证 + session 归属校验
    _ensure_session_owned(user, body.session_id)
    session_manager = getattr(req.app.state, "session_manager", None)
    if session_manager:
        with contextlib.suppress(Exception):  # 会话可能已结束
            await session_manager.transition_phase(
                body.session_id,
                SessionPhase.ENDED,
                reason="customer_ended",
            )
    return {"status": "ok", "session_id": body.session_id}


class ChatTransferRequest(BaseModel):
    session_id: str
    reason: str = "customer_request"


@router.post("/chat/transfer")
async def chat_transfer(body: ChatTransferRequest, req: Request, user: CurrentUser):
    """客户主动请求转人工"""
    # P0-1 第三轮修复: 强制认证 + session 归属校验 (防转人工轰炸)
    _ensure_session_owned(user, body.session_id)
    session_manager = getattr(req.app.state, "session_manager", None)
    if session_manager:
        with contextlib.suppress(Exception):
            await session_manager.transition_phase(
                body.session_id,
                SessionPhase.AGENT,
                new_sub_phase=SessionSubPhase.AG_QUEUED,
                reason=body.reason,
            )

    # 通知 chat-svc 创建转人工会话
    chat_client = getattr(req.app.state, "chat_client", None)
    transfer_url = ""
    if chat_client:
        try:
            result = await chat_client.create_session(body.session_id)
            transfer_url = result.get("transfer_url", "")
        except Exception:
            logger.warning("chat-svc 转人工通知失败: session=%s", body.session_id)

    return {"status": "transferring", "session_id": body.session_id, "transfer_url": transfer_url}


# ── 客户反馈 ──


class ChatFeedbackRequest(BaseModel):
    session_id: str
    message_id: str
    rating: Literal["up", "down", ""] = ""
    comment: str = ""


class GDPRDeleteRequest(BaseModel):
    """P0-4: GDPR 删除请求 (管理端/客户本人发起)."""

    customer_id: str = Field(min_length=1, max_length=64)


@router.post("/chat/feedback")
async def chat_feedback(body: ChatFeedbackRequest, req: Request, user: CurrentUser):
    """客户对 Bot 回复的反馈（点赞/踩/评论）"""
    # P0-1 第三轮修复: 强制认证 + session 归属校验
    _ensure_session_owned(user, body.session_id)
    redis_client = getattr(req.app.state, "redis_client", None)
    if redis_client:
        feedback_key = f"lumio:feedback:customer:{body.message_id}"
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


@router.post("/gdpr/delete")
async def gdpr_delete(body: GDPRDeleteRequest, req: Request, user: CurrentUser):
    """P0-4 第三轮修复: GDPR 删除请求 (客户删除自己的数据).

    认证 + 归属校验: 仅本人 (JWT sub == customer_id) 或 admin/agent 可发起.
    流程: 软删除 (30 天观察) + 立即清理活跃 session, 后台 worker 30 天后硬删除.
    """
    from lumio.shared.auth import AuthorizationError

    # 客户只能删自己; admin/agent 可代删
    if user.role == "customer" and user.user_id != body.customer_id:
        raise AuthorizationError("只能删除本人的数据")

    from lumio.services.common.gdpr import get_gdpr_service

    service = get_gdpr_service()
    req_result = await service.request_deletion(body.customer_id)
    return {"status": "ok", **req_result.to_dict()}


# ── 会话历史 ──


@router.get("/sessions")
async def list_sessions(
    req: Request,
    user: CurrentUser,
    limit: int = 20,
    offset: int = 0,
):
    """列出会话（从 Redis 扫描活跃会话）

    S3 第五轮修复: 按用户过滤 — customer 只返回自己的会话 (meta.customer_id == user_id),
    admin/agent 可全量枚举 (坐席工作台场景). 此前任何登录用户可枚举全系统会话.
    """
    redis_client = getattr(req.app.state, "redis_client", None)
    if not redis_client:
        return {"sessions": [], "total": 0}

    is_admin = user.role in ("admin", "agent")

    # 使用 SCAN 迭代（非阻塞），避免 KEYS 阻塞 Redis
    # P3-5 整改: 用 session_meta_scan_pattern() 公共 helper 代替硬编码
    from lumio.services.common.session import session_meta_scan_pattern

    sessions = []
    async for key in redis_client.scan_iter(match=session_meta_scan_pattern(), count=100):
        raw = await redis_client.get(key)
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except Exception:
            continue
        # customer 仅见自己的会话 (owner 校验)
        if not is_admin and meta.get("customer_id") not in (None, "", user.user_id):
            continue
        sessions.append(
            {
                "session_id": meta.get("session_id"),
                "current_phase": meta.get("current_phase"),
                "sub_phase": meta.get("sub_phase"),
                "turn_count": meta.get("turn_count", 0),
                "last_active_at": meta.get("last_active_at"),
                "agent_id": meta.get("agent_id"),
                "customer_id": meta.get("customer_id") if is_admin else None,  # 脱敏: 客户不可见他人 ID
            }
        )
        if len(sessions) >= offset + limit:
            break
    paged = sessions[offset : offset + limit]
    return {"sessions": paged, "total": len(sessions)}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, req: Request, user: CurrentUser, limit: int = 50):
    """获取会话消息历史"""
    # P0-1 第三轮修复: 强制认证 + session 归属校验 (横向越权读取防护)
    _ensure_session_owned(user, session_id)
    # S3 第五轮修复: customer 读取他人会话时按 meta owner 二次校验 —
    # JWT 无 session_id 声明的 customer token 此前直接放行, 配合枚举可越权
    redis_client = getattr(req.app.state, "redis_client", None)
    if redis_client and user.role == "customer":
        from lumio.services.common.session import session_meta_key

        raw_meta = await redis_client.get(session_meta_key(session_id))
        if raw_meta:
            from lumio.shared.auth import AuthorizationError

            try:
                meta_owner = json.loads(raw_meta).get("customer_id")
                if meta_owner and meta_owner != user.user_id:
                    raise AuthorizationError("无权访问该会话")
            except AuthorizationError:
                raise
            except Exception:
                pass
    if not redis_client:
        # P3-9 整改: 不再静默返回空列表 (会误导客户端 polling loop 空转)
        # 显式 503 走统一错误体, 客户端能识别"系统故障"vs"无消息"
        from lumio.shared.exceptions import ServiceOverloadedError

        raise ServiceOverloadedError("Redis 未就绪, 无法获取会话历史")

    # P3-5 整改: 用 session_history_key() 公共 helper 代替硬编码
    from lumio.services.common.session import session_history_key

    key = session_history_key(session_id)
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
    user: CurrentUser,
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
async def delete_document(
    doc_id: str,
    db: DbSession,
    es_client: ESClientDep,
    milvus_collection: MilvusCollectionDep,
    user: CurrentUser,
):
    """软删除知识库文档，并同步清理 ES 索引与 Milvus 向量

    DB 软删提交后，尽力清理检索侧索引；单边失败不回滚 DB（文档已标记删除），
    但记录 warning 便于补偿，避免"删除后仍被检索到"的数据不一致。
    """
    from sqlalchemy import select

    result = await db.execute(select(KbDocument).where(KbDocument.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        from lumio.shared.exceptions import LumioError

        raise LumioError(code=2001, message=f"文档不存在: {doc_id}")

    doc.is_deleted = True
    doc.deleted_at = datetime.now()
    await db.flush()

    # ── 同步清理 ES 索引（按 doc_id 删除该文档的所有分块）──
    if es_client is not None:
        try:
            from lumio.shared.config import get_settings

            index_name = f"{get_settings().elasticsearch.index_prefix}_kb_chunks"
            await es_client.delete_by_query(
                index=index_name,
                body={"query": {"term": {"doc_id": str(doc_id)}}},
            )
            logger.info("ES 索引清理完成: doc_id=%s", doc_id)
        except Exception as e:
            logger.warning("ES 索引清理失败(doc_id=%s，需补偿): %s", doc_id, e)

    # ── 同步清理 Milvus 向量（按 doc_id 表达式删除）──
    if milvus_collection is not None:
        try:
            milvus_collection.delete(expr=f'doc_id == "{doc_id}"')
            logger.info("Milvus 向量清理完成: doc_id=%s", doc_id)
        except Exception as e:
            logger.warning("Milvus 向量清理失败(doc_id=%s，需补偿): %s", doc_id, e)

    return {"status": "ok", "doc_id": doc_id}


@router.get("/kb/documents/{doc_id}/status")
async def get_document_status(doc_id: str, db: DbSession, user: CurrentUser):
    """查看文档摄入状态"""
    from sqlalchemy import select

    from lumio.shared.orm_models import KbIngestionLog

    result = await db.execute(select(KbDocument).where(KbDocument.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        from lumio.shared.exceptions import LumioError

        raise LumioError(code=2001, message=f"文档不存在: {doc_id}")

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
