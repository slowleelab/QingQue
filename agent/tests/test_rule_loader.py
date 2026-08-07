"""意图规则加载器单元测试 (rule_loader.py)"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from lumio.services.common.rule_loader import (
    CompiledRule,
    RuleLoader,
    _make_fake_rule,
)


class _FakeRule:
    def __init__(self, domain: str, patterns: list[str], keywords: list[str], priority: int = 5) -> None:
        self.domain = domain
        self.patterns = patterns
        self.keywords = keywords
        self.negation_of = None
        self.priority = priority
        self.confidence = 0.9


class _FakeSession:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        result = MagicMock()
        result.scalars.return_value.all.return_value = self._rows
        return result


# ── CompiledRule ──


def test_compiled_rule_compiles():
    """patterns 编译为正则, keywords 转为集合"""
    rule = CompiledRule(_FakeRule("card", ["挂失", "丢卡"], ["额度"], priority=8))
    assert rule.domain == "card"
    assert len(rule.patterns) == 2
    assert rule.keywords == {"额度"}
    assert rule.priority == 8


# ── load_from_db ──


async def test_load_from_db_success():
    """DB 加载 ACTIVE 规则"""
    loader = RuleLoader()
    await loader.load_from_db(lambda: _FakeSession([_FakeRule("card", ["挂失"], [])]))
    assert loader._loaded is True
    assert len(loader.rules) == 1
    assert loader.rules[0].domain == "card"


async def test_load_from_db_error_fallback_empty():
    """DB 异常 → 空规则集"""
    loader = RuleLoader()

    def boom():
        raise RuntimeError("no db")

    await loader.load_from_db(boom)
    assert loader.rules == []


# ── load_from_memory / match ──


def test_load_from_memory_seeds():
    """内存种子规则 8 条"""
    loader = RuleLoader()
    loader.load_from_memory()
    assert loader._loaded is True
    assert len(loader.rules) == 8


def test_match_pattern_hit():
    """正则命中 → (domain, confidence)"""
    loader = RuleLoader()
    loader.load_from_memory()
    hint, conf = loader.match("我的卡丢了怎么办")  # 含"丢卡"子串? 不 — "卡丢了"颠倒
    hint, conf = loader.match("我要挂失")
    assert hint == "card"
    assert conf == 0.9


def test_match_keyword_hit():
    """关键词命中 → confidence - 0.1"""
    loader = RuleLoader()
    loader.load_from_memory()
    hint, conf = loader.match("我想查账单")
    assert hint == "bill"
    assert conf == pytest.approx(0.75)


def test_match_default():
    """未匹配 → default"""
    loader = RuleLoader()
    loader.load_from_memory()
    hint, conf = loader.match("今天天气怎么样啊哈哈哈")
    assert hint == "default"
    assert conf == 0.0


def test_match_empty_rules():
    """空规则集 → default"""
    loader = RuleLoader()
    hint, conf = loader.match("你好")
    assert hint == "default"


# ── start_hot_reload ──


async def test_hot_reload_listens_and_cancels():
    """Pub/Sub 监听启动, 收到通知刷新规则, 取消时清理"""
    import contextlib

    loader = RuleLoader()
    loader.load_from_memory()

    class _FakePubSub:
        def __init__(self) -> None:
            self.unsubscribed = False
            self.closed = False

        async def subscribe(self, channel: str) -> None:
            pass

        def listen(self):
            return _aiter_msgs()

        async def unsubscribe(self, channel: str) -> None:
            self.unsubscribed = True

        async def aclose(self) -> None:
            self.closed = True

    class _FakeRedis:
        def __init__(self) -> None:
            self.ps = _FakePubSub()

        def pubsub(self):
            return self.ps

    async def _aiter_msgs():
        yield {"type": "subscribe"}
        yield {"type": "message", "data": "reload"}
        raise asyncio.CancelledError()

    redis = _FakeRedis()
    before = set(asyncio.all_tasks())
    await loader.start_hot_reload(redis, lambda: _FakeSession([_FakeRule("card", ["挂失"], [])]))
    await asyncio.sleep(0.1)  # _listen 消费消息
    new_tasks = [t for t in asyncio.all_tasks() if t not in before and t is not asyncio.current_task()]
    for t in new_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(t, timeout=1.0)
    # 收到 message 后刷新了规则 (内存规则 8 条 + DB 1 条)
    assert len(loader.rules) == 1
    assert loader.rules[0].domain == "card"
    # 取消路径清理 pubsub
    assert redis.ps.unsubscribed is True
    assert redis.ps.closed is True


# ── _make_fake_rule ──


def test_make_fake_rule():
    """内存规则构造"""
    rule = _make_fake_rule("card", ["挂失"], ["卡"], 0.9)
    assert rule.domain == "card"
    assert rule.status == "ACTIVE"
    assert rule.priority == 5
    assert rule.id is not None
