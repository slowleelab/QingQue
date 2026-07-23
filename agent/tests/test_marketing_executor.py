"""营销执行器情绪规则测试

覆盖 B3 修复：assist_engine 曾硬编码 sentiment=NEUTRAL，导致情绪感知规则
（如"负面情绪谨慎推荐"/"积极情绪优先推荐"）永远不触发。这里直接验证
evaluate_marketing 对不同情绪标签返回不同推荐结果。
"""

from __future__ import annotations

from smartcs.services.assist.marketing_executor import evaluate_marketing
from smartcs.shared.models import IntentLabel, SentimentLabel


class TestMarketingSentimentRules:
    """情绪感知营销规则测试"""

    def test_limit_query_positive_higher_priority_than_negative(self) -> None:
        """额度查询：积极情绪推荐优先级高于负面情绪"""
        positive = evaluate_marketing(intent=IntentLabel.LIMIT_QUERY, sentiment=SentimentLabel.POSITIVE)
        negative = evaluate_marketing(intent=IntentLabel.LIMIT_QUERY, sentiment=SentimentLabel.NEGATIVE)
        assert positive, "积极情绪应有推荐"
        assert negative, "负面情绪仍有(谨慎)推荐"
        assert positive[0].priority > negative[0].priority

    def test_limit_query_negative_uses_cautious_rule(self) -> None:
        """负面情绪触发"降额投诉场景不适合营销"规则(priority=1)"""
        cards = evaluate_marketing(intent=IntentLabel.LIMIT_QUERY, sentiment=SentimentLabel.NEGATIVE)
        assert cards
        assert cards[0].priority == 1

    def test_reward_query_only_positive(self) -> None:
        """积分查询仅积极情绪推荐，中性情绪不推荐"""
        positive = evaluate_marketing(intent=IntentLabel.REWARD_QUERY, sentiment=SentimentLabel.POSITIVE)
        neutral = evaluate_marketing(intent=IntentLabel.REWARD_QUERY, sentiment=SentimentLabel.NEUTRAL)
        assert positive, "积极情绪应推荐积分产品"
        assert neutral == [], "中性情绪不应推荐(规则限定 positive)"

    def test_complaint_never_markets(self) -> None:
        """投诉意图任何情绪都严禁营销"""
        for s in (SentimentLabel.POSITIVE, SentimentLabel.NEUTRAL, SentimentLabel.NEGATIVE, SentimentLabel.ANGRY):
            assert evaluate_marketing(intent=IntentLabel.COMPLAINT, sentiment=s) == []

    def test_sentiment_accepts_string(self) -> None:
        """sentiment 接受字符串（assist_engine 传入 sentiment.value 的场景）"""
        cards = evaluate_marketing(intent="limit_query", sentiment="positive")
        assert cards
        assert cards[0].priority == 4

    def test_low_risk_tolerance_skips(self) -> None:
        """客户风险等级过低(R1)不推荐"""
        cards = evaluate_marketing(
            intent=IntentLabel.LIMIT_QUERY,
            sentiment=SentimentLabel.POSITIVE,
            customer_risk_tolerance="R1",
        )
        assert cards == []
