"""消息审计落库

将聊天消息全量写入 PostgreSQL chat_message 表，提供合规审计和全文搜索能力。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smartcs.shared.orm_models import ChatMessage, ChatMessageStatus

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


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
