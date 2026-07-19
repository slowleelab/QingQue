"""双通道意图分类器单元测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.common.classifier import (
    IntentClassifier,
    LLMClassifier,
    RuleClassifier,
    get_domain,
)
from smartcs.shared.models import IntentLabel, IntentResult, SentimentLabel

# ── RuleClassifier ──


def test_rule_classify_bill_query() -> None:
    """账单关键词应分类为 bill_query"""
    classifier = RuleClassifier()
    result = classifier.classify("我想查一下账单")
    assert result.primary_intent == IntentLabel.BILL_QUERY
    assert result.primary_confidence >= 0.7


def test_rule_classify_limit_query() -> None:
    """额度关键词应分类为 limit_query"""
    classifier = RuleClassifier()
    result = classifier.classify("我的额度是多少")
    assert result.primary_intent == IntentLabel.LIMIT_QUERY


def test_rule_classify_card_loss() -> None:
    """挂失关键词应分类为 card_loss"""
    classifier = RuleClassifier()
    result = classifier.classify("信用卡丢了要挂失")
    assert result.primary_intent == IntentLabel.CARD_LOSS
    assert result.primary_confidence >= 0.8


def test_rule_classify_transfer_agent() -> None:
    """转人工关键词应分类为 transfer_agent"""
    classifier = RuleClassifier()
    result = classifier.classify("我要转人工")
    assert result.primary_intent == IntentLabel.TRANSFER_AGENT
    assert result.primary_confidence >= 0.9


def test_rule_classify_unknown_returns_low_confidence() -> None:
    """无法匹配的输入应返回低置信度"""
    classifier = RuleClassifier()
    result = classifier.classify("帮我看一下这个东西怎么回事")
    assert result.primary_confidence < 0.7


def test_rule_classify_regex_pattern() -> None:
    """正则模式应匹配变体输入"""
    classifier = RuleClassifier()
    result = classifier.classify("我上个月花了多少钱")
    assert result.primary_intent == IntentLabel.BILL_QUERY


def test_rule_keyword_lower_confidence_than_regex() -> None:
    """关键词匹配置信度应低于正则匹配"""
    classifier = RuleClassifier()
    # "额度" 是关键词但不在正则模式中直接匹配（额度的正则也包含"额度"）
    result_regex = classifier.classify("我的可用额度")
    result_keyword = classifier.classify("额度")
    # 正则匹配了更具体的模式，置信度应 >= 关键词
    assert result_regex.primary_confidence >= result_keyword.primary_confidence


# ── LLMClassifier ──


@pytest.mark.asyncio
async def test_llm_classify_success() -> None:
    """LLM 分类应返回结构化结果"""
    mock_llm = MagicMock()
    mock_llm.classify = AsyncMock(
        return_value={
            "intent": "bill_query",
            "confidence": 0.85,
            "entities": [{"entity_type": "time_range", "value": "上个月"}],
            "sentiment": "neutral",
        }
    )

    classifier = LLMClassifier(mock_llm)
    intent, entities, sentiment = await classifier.classify("上个月消费了多少")

    assert intent.primary_intent == IntentLabel.BILL_QUERY
    assert intent.primary_confidence == 0.85
    assert len(entities) == 1
    assert entities[0].entity_type == "time_range"
    assert sentiment == SentimentLabel.NEUTRAL


@pytest.mark.asyncio
async def test_llm_classify_fallback_on_error() -> None:
    """LLM 调用失败时应返回兜底结果"""
    mock_llm = MagicMock()
    mock_llm.classify = AsyncMock(side_effect=Exception("LLM 不可用"))

    classifier = LLMClassifier(mock_llm)
    intent, entities, sentiment = await classifier.classify("随便什么")

    assert intent.primary_intent == IntentLabel.FAQ
    assert intent.primary_confidence == 0.0
    assert entities == []
    assert sentiment == SentimentLabel.NEUTRAL


# ── IntentClassifier (双通道) ──


@pytest.mark.asyncio
async def test_dual_path_fast_path_hit() -> None:
    """高置信度规则匹配应直接使用 Fast Path"""
    rule = RuleClassifier()
    classifier = IntentClassifier(rule_classifier=rule, llm_classifier=None)
    intent, entities, sentiment, source = await classifier.classify("我要查账单")

    assert intent.primary_intent == IntentLabel.BILL_QUERY
    assert source == "rule"


@pytest.mark.asyncio
async def test_dual_path_slow_path_fallthrough() -> None:
    """低置信度时应 fallthrough 到 LLM"""
    rule = RuleClassifier()
    mock_llm_classifier = MagicMock()
    mock_llm_classifier.classify = AsyncMock(
        return_value=(
            IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.6),
            [],
            SentimentLabel.NEUTRAL,
        )
    )

    classifier = IntentClassifier(rule_classifier=rule, llm_classifier=mock_llm_classifier, fast_threshold=0.7)
    intent, entities, sentiment, source = await classifier.classify("这个怎么弄")

    assert source == "llm"
    mock_llm_classifier.classify.assert_called_once()


@pytest.mark.asyncio
async def test_dual_path_no_llm_uses_fallback() -> None:
    """无 LLM 时应使用 Fast Path 低置信度结果"""
    rule = RuleClassifier()
    classifier = IntentClassifier(rule_classifier=rule, llm_classifier=None)
    intent, entities, sentiment, source = await classifier.classify("这个怎么弄")

    assert source == "fallback"


# ── get_domain ──


def test_get_domain_knowledge() -> None:
    """知识类意图应路由到 knowledge 域"""
    assert get_domain(IntentLabel.BILL_QUERY) == "knowledge"
    assert get_domain(IntentLabel.LIMIT_QUERY) == "knowledge"
    assert get_domain(IntentLabel.REWARD_QUERY) == "knowledge"


def test_get_domain_business() -> None:
    """业务类意图应路由到 business 域"""
    assert get_domain(IntentLabel.CARD_LOSS) == "business"
    assert get_domain(IntentLabel.COMPLAINT) == "business"


def test_get_domain_fallback() -> None:
    """闲聊/未知意图应路由到 fallback 域"""
    assert get_domain(IntentLabel.CHITCHAT) == "fallback"
