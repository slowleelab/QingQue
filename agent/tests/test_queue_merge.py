"""调用前队列检查 + 消息合并 单元测试"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestQueueMerge:
    """队列 drain 合并测试"""

    @pytest.mark.asyncio
    async def test_drain_pending_messages(self):
        """队列中有排队消息时，drain 并合并"""
        q = asyncio.Queue()

        # 第一条消息进入队列
        await q.put(
            (
                "msg-001",
                {
                    "session_id": "sess-001",
                    "message_id": "client-001",
                    "message": "我要转账",
                    "customer_id": "cust-001",
                    "channel": "web",
                    "_trace_context": "abc:def:01",
                },
            )
        )

        # 排队消息（模拟快速连续发送）
        await q.put(
            (
                "msg-002",
                {
                    "session_id": "sess-001",
                    "message_id": "client-002",
                    "message": "转到工商银行",
                    "customer_id": "cust-001",
                    "channel": "web",
                    "_trace_context": "",
                },
            )
        )
        await q.put(
            (
                "msg-003",
                {
                    "session_id": "sess-001",
                    "message_id": "client-003",
                    "message": "转5000",
                    "customer_id": "cust-001",
                    "channel": "web",
                    "_trace_context": "",
                },
            )
        )

        # 验证所有消息都在队列中
        assert q.qsize() == 3

        # 模拟 worker 取第一条
        msg_id, fields = await q.get()
        assert fields["message"] == "我要转账"
        assert q.qsize() == 2  # 还有 2 条排队

        # drain 排队消息
        drained: list[tuple] = []
        while not q.empty():
            try:
                drained.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break

        assert len(drained) == 2
        drained_msgs = [f[1]["message"] for f in drained]
        assert "转到工商银行" in drained_msgs
        assert "转5000" in drained_msgs

        # 合并
        merged = fields["message"] + "\n" + "\n".join(f[1]["message"] for f in drained)
        assert "我要转账" in merged
        assert "转到工商银行" in merged
        assert "转5000" in merged

    @pytest.mark.asyncio
    async def test_no_pending_messages_no_merge(self):
        """队列为空时不做合并"""
        q = asyncio.Queue()
        await q.put(
            (
                "msg-001",
                {
                    "session_id": "sess-001",
                    "message_id": "client-001",
                    "message": "你好",
                },
            )
        )

        msg_id, fields = await q.get()
        assert q.empty()  # 没有排队消息

        # 无需 drain
        drained_count = 0
        while not q.empty():
            try:
                q.get_nowait()
                drained_count += 1
            except asyncio.QueueEmpty:
                break

        assert drained_count == 0

    @pytest.mark.asyncio
    async def test_drain_skips_expired_messages(self):
        """drain 时跳过过期消息"""
        q = asyncio.Queue()
        now = asyncio.get_event_loop().time()

        await q.put(
            (
                "msg-001",
                {
                    "message": "我要转账",
                    "_enqueue_time": now - 1,  # 1 秒前，未过期
                },
            )
        )
        await q.put(
            (
                "msg-002",
                {
                    "message": "过期消息",
                    "_enqueue_time": now - 100,  # 100 秒前，已过期（TTL=8s）
                },
            )
        )
        await q.put(
            (
                "msg-003",
                {
                    "message": "转到工行",
                    "_enqueue_time": now - 0.5,
                },
            )
        )

        # drain，跳过过期的 msg-002
        message_ttl = 8  # 8 秒 TTL
        drained_messages = []
        while not q.empty():
            try:
                pending_msg_id, pending_fields = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            pending_enqueue = pending_fields.get("_enqueue_time", 0)
            if pending_enqueue and (now - pending_enqueue > message_ttl):
                continue  # 跳过过期
            drained_messages.append(pending_fields["message"])

        # msg-002（过期）被跳过
        assert drained_messages == ["我要转账", "转到工行"]
        assert len(drained_messages) == 2

    @pytest.mark.asyncio
    async def test_merge_audit_writes_for_drained_messages(self):
        """drain 的消息写入审计记录"""
        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        from smartcs.services.common.audit import write_chat_message

        drained_messages = [
            (
                "msg-002",
                {
                    "message_id": "client-002",
                    "message": "转到工商银行",
                    "customer_id": "cust-001",
                    "channel": "web",
                    "_trace_context": "abc:def:01",
                },
            ),
            (
                "msg-003",
                {
                    "message_id": "client-003",
                    "message": "转5000",
                    "customer_id": "cust-001",
                    "channel": "web",
                    "_trace_context": "",
                },
            ),
        ]

        for _, fields in drained_messages:
            await write_chat_message(
                mock_session_factory,
                session_id="sess-001",
                message_id=fields["message_id"],
                content=fields["message"],
                customer_id=fields.get("customer_id", ""),
                channel=fields.get("channel", "web"),
                quick_intent="default",
            )

        # 每个 drain 的消息各写入一次
        assert mock_session.add.call_count == 2

    @pytest.mark.asyncio
    async def test_merged_audit_updated_with_merged_source(self):
        """合并消息审计更新为 source=merged"""
        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        from smartcs.services.common.audit import update_chat_message
        from smartcs.shared.orm_models import ChatMessageStatus

        merged_ids = ["client-002", "client-003"]
        for merged_id in merged_ids:
            await update_chat_message(
                mock_session_factory,
                merged_id,
                processing_status=ChatMessageStatus.DONE,
                intent="transfer",
                source="merged",
            )

        assert mock_session.execute.call_count == 2
        assert mock_session.commit.call_count == 2
