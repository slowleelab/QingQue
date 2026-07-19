"""审计日志单元测试

验证 ChatMessage 写入、更新、状态转换等功能。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.common.audit import update_chat_message, write_chat_message
from smartcs.shared.orm_models import ChatMessage, ChatMessageStatus


class TestWriteChatMessage:
    """消息审计写入测试"""

    @pytest.mark.asyncio
    async def test_write_success(self):
        """正常写入一条审计记录"""
        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        result = await write_chat_message(
            mock_session_factory,
            session_id="sess-001",
            message_id="msg-001",
            content="我要查询账单",
            customer_id="cust-001",
            channel="web",
            quick_intent="bill_query",
            trace_id="abc123",
        )

        assert result is not None
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_minimal_fields(self):
        """仅必填字段写入"""
        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        result = await write_chat_message(
            mock_session_factory,
            session_id="sess-002",
            message_id="msg-002",
            content="你好",
        )

        assert result is not None
        args = mock_session.add.call_args[0][0]
        assert args.session_id == "sess-002"
        assert args.message_id == "msg-002"
        assert args.content == "你好"
        assert args.processing_status == ChatMessageStatus.QUEUED
        assert args.channel == "web"

    @pytest.mark.asyncio
    async def test_write_db_error_returns_none(self):
        """数据库异常时返回 None，不抛异常"""
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.side_effect = RuntimeError("DB down")

        result = await write_chat_message(
            mock_session_factory,
            session_id="sess-003",
            message_id="msg-003",
            content="测试",
        )

        assert result is None


class TestUpdateChatMessage:
    """消息审计更新测试"""

    @pytest.mark.asyncio
    async def test_update_status_to_done(self):
        """更新消息状态为 done"""
        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        await update_chat_message(
            mock_session_factory,
            "msg-001",
            processing_status=ChatMessageStatus.DONE,
            intent="bill_query",
            source="llm",
            processing_duration_ms=350,
        )

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_status_to_error(self):
        """更新消息状态为 error"""
        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        await update_chat_message(
            mock_session_factory,
            "msg-001",
            processing_status=ChatMessageStatus.ERROR,
            error_message="Agent crashed",
            processing_duration_ms=5000,
        )

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_partial_fields(self):
        """仅更新部分字段"""
        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        await update_chat_message(
            mock_session_factory,
            "msg-001",
            processing_status=ChatMessageStatus.PROCESSING,
        )

        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_no_fields_short_circuits(self):
        """没有提供任何更新字段时，不执行 SQL"""
        mock_session_factory = MagicMock()

        await update_chat_message(mock_session_factory, "msg-001")

        # session_factory 不应该被调用
        mock_session_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_db_error_silent(self):
        """数据库异常时静默处理，不抛异常"""
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.side_effect = RuntimeError("DB down")

        # 不应抛异常
        await update_chat_message(
            mock_session_factory,
            "msg-001",
            processing_status=ChatMessageStatus.DONE,
        )


class TestChatMessageModel:
    """ChatMessage 模型基础测试"""

    def test_default_values(self):
        """构造时可以显式指定 processing_status"""
        msg = ChatMessage(
            session_id="sess-001",
            message_id="msg-001",
            content="测试消息",
            processing_status=ChatMessageStatus.QUEUED,
            channel="web",
        )
        assert msg.processing_status == ChatMessageStatus.QUEUED
        assert msg.channel == "web"

    def test_status_enum_values(self):
        """状态枚举值正确"""
        assert ChatMessageStatus.QUEUED.value == "queued"
        assert ChatMessageStatus.PROCESSING.value == "processing"
        assert ChatMessageStatus.DONE.value == "done"
        assert ChatMessageStatus.SKIPPED.value == "skipped"
        assert ChatMessageStatus.ERROR.value == "error"

    def test_optional_fields_default_none(self):
        """可选字段默认为 None"""
        msg = ChatMessage(
            session_id="sess-001",
            message_id="msg-001",
            content="测试",
        )
        assert msg.quick_intent is None
        assert msg.intent is None
        assert msg.source is None
        assert msg.trace_id is None
        assert msg.error_message is None
        assert msg.metadata_json is None
        assert msg.customer_id is None
        assert msg.processing_duration_ms is None


class TestAuditIntegration:
    """审计日志完整流程测试"""

    @pytest.mark.asyncio
    async def test_full_lifecycle_write_then_update(self):
        """完整生命周期：写入 → 更新 → 完成"""
        session = AsyncMock()
        session_factory = MagicMock()
        session_factory.return_value.__aenter__.return_value = session

        # 1. 写入初始记录
        record = await write_chat_message(
            session_factory,
            session_id="sess-lifecycle",
            message_id="msg-lifecycle",
            content="生命周期测试",
            quick_intent="bill_query",
        )
        assert record is not None

        # 2. 更新为 done
        await update_chat_message(
            session_factory,
            "msg-lifecycle",
            processing_status=ChatMessageStatus.DONE,
            intent="bill_query",
            source="llm",
        )

        assert session.add.call_count == 1
        assert session.execute.call_count == 1
        assert session.commit.call_count == 2
