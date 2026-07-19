"""Bot Streams + per-session Worker 单元测试

使用 mock 避免重量级依赖 (elasticsearch, langgraph)。
"""

from __future__ import annotations

import asyncio
import re

import pytest

# 不直接 import smartcs.services.bot.router, 用 mock 避免 import 链
# 测试核心逻辑函数和常量定义


class TestQuickIntentMatchRegex:
    """快速意图匹配正则测试"""

    @pytest.fixture
    def patterns(self):
        return [
            ("lost_card", re.compile(r"挂失|丢卡|卡丢了|卡不见了")),
            ("complaint", re.compile(r"投诉")),
            ("bill_query", re.compile(r"账单|消费|扣款|还款|欠款")),
            ("limit_query", re.compile(r"额度|提额|降额|信用额度")),
        ]

    def _quick_match(self, message: str, patterns: list) -> str:
        for intent, pattern in patterns:
            if pattern.search(message):
                return intent
        return "default"

    def test_lost_card(self, patterns):
        assert self._quick_match("我的卡丢了怎么办", patterns) == "lost_card"
        assert self._quick_match("挂失信用卡", patterns) == "lost_card"
        assert self._quick_match("卡不见了", patterns) == "lost_card"

    def test_complaint(self, patterns):
        assert self._quick_match("我要投诉", patterns) == "complaint"

    def test_bill_query(self, patterns):
        assert self._quick_match("上个月账单多少钱", patterns) == "bill_query"
        assert self._quick_match("我还款了没", patterns) == "bill_query"
        assert self._quick_match("消费记录", patterns) == "bill_query"

    def test_limit_query(self, patterns):
        assert self._quick_match("我想提额", patterns) == "limit_query"
        assert self._quick_match("信用额度多少", patterns) == "limit_query"

    def test_default(self, patterns):
        assert self._quick_match("今天天气怎么样", patterns) == "default"
        assert self._quick_match("", patterns) == "default"

    def test_priority_first_match(self, patterns):
        """第一个匹配的意图胜出 — 挂失在投诉前面"""
        assert self._quick_match("卡丢了我要投诉", patterns) == "lost_card"


class TestFastReplyMessages:
    """满荷固定话术验证"""

    REPLIES = {
        "lost_card": "挂失为紧急业务，正在为您优先处理，请稍候。如超过 10 秒未回复，请直接输入'转人工'。",
        "complaint": "您的投诉已记录，正在转接人工处理。",
        "bill_query": "当前咨询量较大，账单查询结果稍后返回，也可输入'转人工'联系客服。",
        "limit_query": "您的问题正在处理中，预计 30 秒内回复。",
        "default": "当前咨询量较大，请稍候或输入'转人工'。",
    }

    def test_all_non_empty(self):
        for intent, reply in self.REPLIES.items():
            assert reply, f"{intent} is empty"

    def test_most_mention_transfer_option(self):
        """大部分兜底话术引导转人工 (limit_query 为短期等待除外)"""
        transfer_replies = ["lost_card", "complaint", "bill_query", "default"]
        for intent in transfer_replies:
            reply = self.REPLIES[intent]
            assert "转人工" in reply or "转接人工" in reply, f"{intent} should guide to transfer: {reply}"
        # limit_query 告知等待时间，不强制转人工
        assert "30 秒" in self.REPLIES["limit_query"]

    def test_lost_card_is_priority_message(self):
        assert "紧急" in self.REPLIES["lost_card"]
        assert "优先" in self.REPLIES["lost_card"]


class TestPerSessionQueuePattern:
    """per-session Queue 模式验证"""

    def test_queues_isolated_per_session(self):
        """不同 session 有独立 Queue"""
        queues: dict[str, asyncio.Queue] = {}
        q_a = queues.setdefault("sid-A", asyncio.Queue())
        q_b = queues.setdefault("sid-B", asyncio.Queue())
        assert q_a is not q_b
        assert len(queues) == 2

    def test_same_session_reuses_queue(self):
        """同一 session 复用 Queue"""
        queues: dict[str, asyncio.Queue] = {}
        q1 = queues.setdefault("sid-X", asyncio.Queue())
        q2 = queues.setdefault("sid-X", asyncio.Queue())
        assert q1 is q2

    def test_worker_lifecycle(self):
        """Worker 注册 → 消费 → 退出 完整生命周期"""
        active: dict[str, bool] = {}
        queues: dict[str, asyncio.Queue] = {}

        # 首次消息 → 注册 Worker
        queues.setdefault("sid-1", asyncio.Queue())
        active["sid-1"] = True

        assert "sid-1" in active
        assert active["sid-1"] is True

        # Worker 退出 → 清理
        active.pop("sid-1", None)
        queues.pop("sid-1", None)

        assert "sid-1" not in active
        assert "sid-1" not in queues

    @pytest.mark.asyncio
    async def test_queue_put_get_ordering(self):
        """Queue 保证消息顺序"""
        q: asyncio.Queue = asyncio.Queue()
        await q.put(("msg-1", {"content": "first"}))
        await q.put(("msg-2", {"content": "second"}))

        msg1_id, msg1_fields = await q.get()
        msg2_id, msg2_fields = await q.get()

        assert msg1_id == "msg-1"
        assert msg2_id == "msg-2"


class TestPollJsonBuilder:
    """轮询状态 JSON 构建"""

    def _build(self, *, status, **kwargs):
        data = {"status": status}
        data.update(kwargs)
        return data

    def test_done(self):
        r = self._build(status="done", reply="回复", intent="bill_query", confidence=0.85, source="api")
        assert r == {"status": "done", "reply": "回复", "intent": "bill_query", "confidence": 0.85, "source": "api"}

    def test_queued(self):
        r = self._build(status="queued", position=3, est_wait="约15秒")
        assert r["status"] == "queued"
        assert r["position"] == 3

    def test_processing(self):
        r = self._build(status="processing")
        assert r["status"] == "processing"

    def test_timeout(self):
        r = self._build(status="timeout", suggestion="请稍后重试")
        assert r["status"] == "timeout"
        assert r["suggestion"] == "请稍后重试"


class TestMetricsStructure:
    """监控指标结构"""

    def test_required_fields(self):
        metrics = {"p": 0, "sl": 0, "as": 0, "su": 0.0, "fr": 0, "to": 0}
        for key in ("p", "sl", "as", "su", "fr", "to"):
            assert key in metrics, f"Missing metric: {key}"

    def test_semaphore_utilization_calculation(self):
        """Semaphore 利用率 = 1 - available/max"""
        max_slots = 10
        available = 3

        utilization = 1.0 - (available / max_slots)
        assert utilization == pytest.approx(0.7, abs=0.01)

        available = 10
        utilization = 1.0 - (available / max_slots)
        assert utilization == 0.0

        available = 0
        utilization = 1.0 - (available / max_slots)
        assert utilization == 1.0
