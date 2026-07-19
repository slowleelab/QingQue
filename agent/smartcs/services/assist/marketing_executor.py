"""E2 营销执行器

根据客户画像 + 对话意图 + 情绪推荐合适的金融产品。
当前为规则引擎版本，生产可对接营销推荐系统 (gRPC)。

产品来源: ProductCatalog (内存/DB)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from smartcs.shared.models import IntentLabel, SentimentLabel

logger = logging.getLogger(__name__)


@dataclass
class MarketingCard:
    """营销推荐卡片"""

    product_id: str
    product_name: str
    product_type: str
    reason: str
    priority: int  # 1-5, 5 最高
    risk_tip: str = ""
    action_url: str = ""


# 推荐策略规则
_MATCH_RULES: list[dict] = [
    # (客户意图, 情绪条件, 产品标签, 推荐优先级)
    {
        "intent": "reward_query",
        "sentiment": "positive",
        "tag": "积分",
        "priority": 5,
        "reason": "客户对积分感兴趣且情绪积极，推荐积分兑换相关产品",
    },
    {
        "intent": "bill_query",
        "sentiment": "neutral",
        "tag": "分期",
        "priority": 3,
        "reason": "账单查询可能是分期需求的前兆",
    },
    {
        "intent": "bill_query",
        "sentiment": "negative",
        "tag": "分期",
        "priority": 2,
        "reason": "账单压力大时谨慎推荐分期，需注明手续费",
    },
    {
        "intent": "limit_query",
        "sentiment": "positive",
        "tag": "提额",
        "priority": 4,
        "reason": "提额需求且情绪积极，可推荐高端卡产品",
    },
    {
        "intent": "limit_query",
        "sentiment": "negative",
        "tag": "提额",
        "priority": 1,
        "reason": "降额投诉场景不适合营销",
    },
    {
        "intent": "faq",
        "sentiment": "positive",
        "tag": "理财",
        "priority": 3,
        "reason": "客户咨询且情绪积极，可能对理财产品有兴趣",
    },
    {"intent": "complaint", "sentiment": "any", "tag": "", "priority": 0, "reason": "投诉场景严禁营销"},
    {"intent": "lost_card", "sentiment": "any", "tag": "", "priority": 0, "reason": "挂失场景严禁营销"},
]


def evaluate_marketing(
    intent: IntentLabel | str,
    sentiment: SentimentLabel | str,
    customer_risk_tolerance: str = "R2",
    suppress_rounds: int = 0,
) -> list[MarketingCard]:
    """评估是否需要推送营销卡片

    Args:
        intent: 当前用户意图
        sentiment: 用户情绪
        customer_risk_tolerance: 客户风险承受等级 (R1-R5)
        suppress_rounds: 剩余压制轮数 (>0 时暂停营销)

    Returns:
        营销卡片列表（可能为空）
    """
    if suppress_rounds > 0:
        logger.debug("营销压制中: suppress_rounds=%d", suppress_rounds)
        return []

    intent_str = intent.value if hasattr(intent, "value") else str(intent)
    sentiment_str = sentiment.value if hasattr(sentiment, "value") else str(sentiment)

    cards: list[MarketingCard] = []

    for rule in _MATCH_RULES:
        if rule["priority"] == 0:
            # 禁止营销的意图
            if rule["intent"] == intent_str:
                logger.debug("营销跳过: intent=%s (禁止营销)", intent_str)
                return []
            continue

        if rule["intent"] != intent_str:
            continue
        if rule["sentiment"] != "any" and rule["sentiment"] != sentiment_str:
            continue

        # 检查客户风险承受等级是否匹配
        risk_level = int(customer_risk_tolerance[1]) if customer_risk_tolerance.startswith("R") else 2
        if risk_level < 2:
            logger.debug("营销跳过: 客户风险等级过低 R%d", risk_level)
            continue

        # 根据产品目录生成卡片
        card = _build_card(rule["tag"], rule["priority"], rule["reason"])
        if card:
            cards.append(card)

    return cards


def _build_card(product_tag: str, priority: int, reason: str) -> MarketingCard | None:
    """根据标签构建营销卡片"""

    # 简化的产品推荐（生产环境从 ProductCatalog 查询）
    product_map = {
        "积分": ("prod-001", "积分超值兑", "积分兑换", "立即兑换 >"),
        "分期": ("prod-002", "账单轻松分期", "分期", "手续费0.6%/月，立即办理 >"),
        "提额": ("prod-003", "额度升级计划", "提额", "根据您的用卡记录，可申请提额 >"),
        "理财": ("prod-004", "闲钱理财优选", "理财", "年化收益参考，立即了解 >"),
    }

    if product_tag not in product_map:
        return None

    pid, name, ptype, action = product_map[product_tag]

    risk_tips = {
        "分期": "投资有风险，分期需量力而行。手续费以实际审批为准。",
        "理财": "理财非存款，产品有风险，投资须谨慎。",
        "提额": "额度调整以银行最终审批结果为准。",
    }

    return MarketingCard(
        product_id=pid,
        product_name=name,
        product_type=ptype,
        reason=reason,
        priority=priority,
        risk_tip=risk_tips.get(product_tag, ""),
        action_url=action,
    )
