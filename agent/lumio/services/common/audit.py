"""消息审计落库

将聊天消息全量写入 PostgreSQL chat_message 表，提供合规审计和全文搜索能力。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lumio.shared.orm_models import AuditLog, ChatMessage, ChatMessageStatus

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


async def write_audit_log(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    actor_id: str,
    actor_role: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
    method: str = "TOOL",
    path: str = "",
    status_code: int = 200,
) -> AuditLog | None:
    """写入操作审计日志（append-only）

    用于工具调用、确认/取消决策等非 HTTP 触发的状态变更审计。
    ``detail`` 应为**已脱敏**内容（调用方负责脱敏）。

    Args:
        actor_id: 操作者 ID（customer_id / user_id / service）
        actor_role: 角色 customer/agent/admin/service
        action: 操作类型，如 ``tool.card_loss`` / ``tool.confirm``
        target_type: 目标类型，如 session/tool
        target_id: 目标 ID（会话 ID 等）
        detail: 已脱敏的操作详情（入参/出参摘要、决策等）
        method: 合成方法标识，工具调用默认 "TOOL"
        path: 合成路径，默认 ``/tool/{action}``
        status_code: 结果码，成功 200 / 失败 500

    Returns:
        AuditLog 对象，失败返回 None（审计失败不阻断主链路）
    """
    try:
        async with session_factory() as session:
            record = AuditLog(
                actor_id=actor_id or "unknown",
                actor_role=actor_role or "service",
                action=action,
                target_type=target_type,
                target_id=target_id,
                method=method,
                path=path or f"/tool/{action}",
                status_code=status_code,
                detail=detail,
            )
            session.add(record)
            await session.commit()
            return record
    except Exception as exc:  # 审计失败不应阻断主链路
        logger.warning("写入审计日志失败: action=%s, error=%s", action, exc)
        return None


async def write_chat_message(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    message_id: str,
    content: str,
    customer_id: str = "",
    channel: str = "web",
    quick_intent: str | None = None,
    trace_id: str | None = None,
) -> ChatMessage | None:
    """写入消息审计记录（初始状态 queued）

    Returns:
        ChatMessage 对象，失败返回 None
    """
    try:
        async with session_factory() as session:
            record = ChatMessage(
                session_id=session_id,
                message_id=message_id,
                customer_id=customer_id or "",
                channel=channel,
                content=content,
                quick_intent=quick_intent,
                processing_status=ChatMessageStatus.QUEUED,
                trace_id=trace_id,
            )
            session.add(record)
            await session.commit()
            return record
    except Exception:
        logger.exception("写入消息审计失败: message_id=%s", message_id)
        return None


async def update_chat_message(
    session_factory: async_sessionmaker[AsyncSession],
    message_id: str,
    *,
    processing_status: ChatMessageStatus | None = None,
    intent: str | None = None,
    source: str | None = None,
    processing_duration_ms: int | None = None,
    error_message: str | None = None,
) -> None:
    """更新消息审计记录

    Args:
        session_factory: 数据库会话工厂
        message_id: 消息唯一 ID
        processing_status: 处理状态
        intent: 最终识别的意图
        source: 回复来源（llm/retrieval/template/fast_reply/fallback/error_fallback）
        processing_duration_ms: 处理耗时
        error_message: 错误详情
    """
    values: dict = {}
    if processing_status is not None:
        values["processing_status"] = processing_status
    if intent is not None:
        values["intent"] = intent
    if source is not None:
        values["source"] = source
    if processing_duration_ms is not None:
        values["processing_duration_ms"] = processing_duration_ms
    if error_message is not None:
        values["error_message"] = error_message

    if not values:
        return

    try:
        async with session_factory() as session:
            from sqlalchemy import update

            stmt = update(ChatMessage).where(ChatMessage.message_id == message_id).values(**values)
            await session.execute(stmt)
            await session.commit()
    except Exception:
        logger.exception("更新消息审计失败: message_id=%s", message_id)
