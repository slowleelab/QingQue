"""L1 意图检测规则加载器

从 PostgreSQL 加载规则，支持 Redis Pub/Sub 热刷新。
替换 bot/router.py 中硬编码的 _FAST_INTENT_PATTERNS。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from smartcs.shared.orm_models import IntentDetectionRule

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

_RULES_RELOAD_CHANNEL = "smartcs:rules:reload"


class CompiledRule:
    """编译后的单条规则"""

    def __init__(self, rule: IntentDetectionRule) -> None:
        self.domain = rule.domain
        self.patterns = [re.compile(p) for p in (rule.patterns or [])]
        self.keywords = set(rule.keywords or [])
        self.negation_of = rule.negation_of
        self.priority = rule.priority
        self.confidence = rule.confidence


class RuleLoader:
    """意图检测规则加载器

    启动时从 DB 加载 ACTIVE 规则，Redis Pub/Sub 监听变更通知热刷新。
    """

    def __init__(self) -> None:
        self._rules: list[CompiledRule] = []
        self._loaded = False

    @property
    def rules(self) -> list[CompiledRule]:
        return self._rules

    async def load_from_db(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """从 PostgreSQL 加载 ACTIVE 规则"""
        try:
            from sqlalchemy import select

            async with session_factory() as session:
                result = await session.execute(
                    select(IntentDetectionRule)
                    .where(IntentDetectionRule.status == "ACTIVE")
                    .order_by(IntentDetectionRule.priority.desc())
                )
                rows = result.scalars().all()
                self._rules = [CompiledRule(r) for r in rows]
                self._loaded = True
                logger.info("L1 规则加载完成: %d 条", len(self._rules))
        except Exception:
            logger.exception("L1 规则加载失败，使用空规则集")
            self._rules = []

    def load_from_memory(self) -> None:
        """内存种子规则（DB 不可用时兜底）"""
        self._rules = [
            CompiledRule(_make_fake_rule("card", ["挂失", "丢卡", "补卡"], [], 0.9)),
            CompiledRule(_make_fake_rule("complaint", ["投诉", "举报"], [], 0.9)),
            CompiledRule(_make_fake_rule("transfer", ["转人工", "人工客服"], [], 0.95)),
            CompiledRule(_make_fake_rule("bill", [], ["账单", "消费", "还款"], 0.85)),
            CompiledRule(_make_fake_rule("limit", [], ["额度", "提额"], 0.85)),
            CompiledRule(_make_fake_rule("installment", [], ["分期"], 0.85)),
            CompiledRule(_make_fake_rule("reward", [], ["积分"], 0.85)),
            CompiledRule(_make_fake_rule("chitchat", ["你好", "在吗", "谢谢", "再见"], [], 0.8)),
        ]
        self._loaded = True

    def match(self, text: str) -> tuple[str, float]:
        """快速意图匹配（<5ms）

        Returns:
            (intent_hint, confidence): intent_hint 为意图标签字符串，未匹配返回 "default"
        """
        for rule in self._rules:
            for pattern in rule.patterns:
                if pattern.search(text):
                    return rule.domain, rule.confidence
            for kw in rule.keywords:
                if kw in text:
                    return rule.domain, rule.confidence - 0.1
        return "default", 0.0

    async def start_hot_reload(
        self,
        redis_client: aioredis.Redis,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """启动 Redis Pub/Sub 热刷新监听（后台协程）"""
        import asyncio

        async def _listen():
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(_RULES_RELOAD_CHANNEL)
            logger.info("L1 规则热加载监听已启动: channel=%s", _RULES_RELOAD_CHANNEL)
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        logger.info("收到规则热加载通知，正在刷新规则...")
                        try:
                            await self.load_from_db(session_factory)
                            logger.info("L1 规则热加载完成: %d 条规则", len(self.rules))
                        except Exception as e:
                            logger.warning("L1 规则热加载失败: %s", e)
            except asyncio.CancelledError:
                await pubsub.unsubscribe(_RULES_RELOAD_CHANNEL)
                raise

        asyncio.create_task(_listen())


def _make_fake_rule(domain: str, patterns: list[str], keywords: list[str], confidence: float) -> IntentDetectionRule:
    """构造内存规则（DB 不可用时兜底）"""
    import uuid_utils

    rule = IntentDetectionRule()
    rule.rule_id = f"seed_{domain}"
    rule.domain = domain
    rule.patterns = patterns
    rule.keywords = keywords
    rule.negation_of = None
    rule.priority = 5
    rule.confidence = confidence
    rule.status = "ACTIVE"
    rule.id = uuid_utils.uuid7()
    return rule
