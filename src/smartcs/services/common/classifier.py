"""双通道意图分类器

Fast Path: RuleClassifier（正则 + 关键词 + 模板匹配），覆盖高频意图
Slow Path: LLMClassifier（Qwen2.5-7B via Ollama，json_mode + few-shot），覆盖模糊/长尾意图

Fast Path 置信度 < 阈值时自动 fallthrough 到 Slow Path。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from smartcs.shared.config import get_settings
from smartcs.shared.models import Entity, IntentLabel, IntentResult, SentimentLabel

if TYPE_CHECKING:
    from smartcs.services.common.llm import LLMClient

if TYPE_CHECKING:
    from smartcs.services.common.llm import LLMClient

logger = logging.getLogger(__name__)

# 规则分类器阈值：Fast Path 置信度 >= 此值直接使用
_FAST_PATH_THRESHOLD = 0.7

# 意图域映射：用于 supervisor 路由
INTENT_DOMAINS: dict[IntentLabel, str] = {
    IntentLabel.BILL_QUERY: "knowledge",
    IntentLabel.TRANSACTION_QUERY: "knowledge",
    IntentLabel.LIMIT_QUERY: "knowledge",
    IntentLabel.INSTALLMENT_INQUIRY: "knowledge",
    IntentLabel.REWARD_QUERY: "knowledge",
    IntentLabel.FAQ: "knowledge",
    IntentLabel.CARD_LOSS: "business",
    IntentLabel.COMPLAINT: "business",
    IntentLabel.TRANSFER_AGENT: "business",
    IntentLabel.CHITCHAT: "fallback",
}

# Fast Path 规则定义
# 每条规则包含: intent, patterns (正则), keywords (关键词), confidence
_RULES: list[dict[str, Any]] = [
    # 账单类
    {
        "intent": IntentLabel.BILL_QUERY,
        "patterns": [r"账单", r"消费记录", r"还款金额", r"本期账单", r"上个?月.?花了多少"],
        "keywords": ["账单", "消费", "还款", "欠款", "应还", "最低还款"],
        "confidence": 0.85,
    },
    # 交易查询
    {
        "intent": IntentLabel.TRANSACTION_QUERY,
        "patterns": [r"交易记录", r"明细", r"流水", r"扣款"],
        "keywords": ["交易", "明细", "流水", "扣款", "刷卡"],
        "confidence": 0.85,
    },
    # 额度类
    {
        "intent": IntentLabel.LIMIT_QUERY,
        "patterns": [r"额度", r"可用额度", r"信用额度", r"提额", r"降额"],
        "keywords": ["额度", "可用", "信用", "提额", "临时额度"],
        "confidence": 0.85,
    },
    # 分期类
    {
        "intent": IntentLabel.INSTALLMENT_INQUIRY,
        "patterns": [r"分期", r"期数", r"手续费率", r"账单分期", r"消费分期"],
        "keywords": ["分期", "期数", "手续费", "分期费率"],
        "confidence": 0.85,
    },
    # 积分类
    {
        "intent": IntentLabel.REWARD_QUERY,
        "patterns": [r"积分", r"积分兑换", r"积分过期", r"积分余额"],
        "keywords": ["积分", "兑换", "过期", "积分商城"],
        "confidence": 0.85,
    },
    # FAQ
    {
        "intent": IntentLabel.FAQ,
        "patterns": [r"什么是", r"怎么办理", r"如何操作", r"流程是什么"],
        "keywords": [],
        "confidence": 0.7,
    },
    # 挂失
    {
        "intent": IntentLabel.CARD_LOSS,
        "patterns": [r"挂失", r"补卡", r"换卡", r"卡片丢失"],
        "keywords": ["挂失", "丢失", "补卡", "换卡"],
        "confidence": 0.9,
    },
    # 投诉
    {
        "intent": IntentLabel.COMPLAINT,
        "patterns": [r"投诉", r"不满意", r"举报", r"投诉你们"],
        "keywords": ["投诉", "不满", "举报"],
        "confidence": 0.9,
    },
    # 转人工
    {
        "intent": IntentLabel.TRANSFER_AGENT,
        "patterns": [r"转人工", r"人工客服", r"找人工", r"我要找.*人"],
        "keywords": ["人工", "转人工", "真人"],
        "confidence": 0.95,
    },
    # 闲聊
    {
        "intent": IntentLabel.CHITCHAT,
        "patterns": [r"你好", r"嗨", r"在吗", r"你是谁", r"谢谢", r"再见"],
        "keywords": [],
        "confidence": 0.8,
    },
]

# LLM 分类 Prompt
_CLASSIFY_SYSTEM_PROMPT = """你是一个银行信用卡客服意图分类器。根据用户输入，输出 JSON 格式的分类结果。

## 输出格式
```json
{
  "intent": "意图标签",
  "confidence": 0.0-1.0的置信度,
  "entities": [{"entity_type": "类型", "value": "值"}],
  "sentiment": "positive/neutral/negative/angry"
}
```

## 可选意图标签
- bill_query: 账单查询
- transaction_query: 交易记录查询
- limit_query: 额度查询
- installment_inquiry: 分期咨询
- reward_query: 积分查询
- faq: 常见问题
- card_loss: 挂失/补卡
- complaint: 投诉
- transfer_agent: 转人工
- chitchat: 闲聊

## 示例
用户: 我上个月花了多少钱
输出: {"intent": "bill_query", "confidence": 0.9, "entities": [{"entity_type": "time_range", "value": "上个月"}], "sentiment": "neutral"}

用户: 额度太低了能不能提一下
输出: {"intent": "limit_query", "confidence": 0.85, "entities": [{"entity_type": "action", "value": "提额"}], "sentiment": "neutral"}

用户: 你们的年费怎么这么贵，我要投诉
输出: {"intent": "complaint", "confidence": 0.95, "entities": [{"entity_type": "topic", "value": "年费"}], "sentiment": "angry"}

用户: 你好呀
输出: {"intent": "chitchat", "confidence": 0.9, "entities": [], "sentiment": "positive"}

## 要求
- 只输出 JSON，不要其他文字
- 置信度 0-1 之间，不确定时给低分
- 模糊输入给 intent="faq"，confidence < 0.5
"""


class RuleClassifier:
    """规则分类器（Fast Path）

    正则匹配 + 关键词匹配，返回最高置信度的意图。
    """

    def __init__(self, rules: list[dict[str, Any]] | None = None) -> None:
        self._rules = rules or _RULES
        # 预编译正则
        self._compiled: list[dict[str, Any]] = []
        for rule in self._rules:
            compiled_patterns = [re.compile(p) for p in rule.get("patterns", [])]
            self._compiled.append({
                "intent": rule["intent"],
                "patterns": compiled_patterns,
                "keywords": rule.get("keywords", []),
                "confidence": rule.get("confidence", 0.7),
            })

    def classify(self, text: str) -> IntentResult:
        """对用户输入进行规则分类

        匹配逻辑：最长匹配优先，多意图时取置信度最高的。
        关键词匹配置信度略低于正则匹配。

        Args:
            text: 用户输入文本

        Returns:
            IntentResult（primary_confidence 低于阈值时触发 Slow Path）
        """
        best_intent = IntentLabel.FAQ
        best_confidence = 0.0
        matched_entities: list[Entity] = []

        for rule in self._compiled:
            matched = False
            rule_confidence = rule["confidence"]

            # 正则匹配
            for pattern in rule["patterns"]:
                match = pattern.search(text)
                if match:
                    matched = True
                    # 正则匹配使用规则设定的置信度
                    break

            # 关键词匹配（置信度降 0.1）
            if not matched:
                for keyword in rule["keywords"]:
                    if keyword in text:
                        matched = True
                        rule_confidence -= 0.1
                        break

            if matched and rule_confidence > best_confidence:
                best_intent = rule["intent"]
                best_confidence = rule_confidence

        return IntentResult(
            primary_intent=best_intent,
            primary_confidence=best_confidence,
        )


class LLMClassifier:
    """LLM 分类器（Slow Path）

    通过 Qwen2.5-7B 的 json_mode 输出结构化分类结果。
    同时提取实体和情感分析。
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def classify(self, text: str) -> tuple[IntentResult, list[Entity], SentimentLabel]:
        """LLM 意图分类

        Args:
            text: 用户输入文本

        Returns:
            (IntentResult, 实体列表, 情感标签)
        """
        try:
            result = await self._llm.classify(
                system_prompt=_CLASSIFY_SYSTEM_PROMPT,
                user_input=text,
            )
        except Exception:
            logger.warning("LLM 分类调用失败，返回兜底结果")
            return (
                IntentResult(primary_intent=IntentLabel.FAQ, primary_confidence=0.0),
                [],
                SentimentLabel.NEUTRAL,
            )

        intent_label = _parse_intent(result.get("intent", ""))
        confidence = result.get("confidence", 0.0)
        entities = _parse_entities(result.get("entities", []))
        sentiment = _parse_sentiment(result.get("sentiment", ""))

        return (
            IntentResult(primary_intent=intent_label, primary_confidence=confidence),
            entities,
            sentiment,
        )


class IntentClassifier:
    """双通道意图分类编排器

    Fast Path（规则） → 置信度 >= 阈值 → 直接使用
                       → 置信度 < 阈值 → Slow Path（LLM）
    """

    def __init__(
        self,
        rule_classifier: RuleClassifier | None = None,
        llm_classifier: LLMClassifier | None = None,
        fast_threshold: float = _FAST_PATH_THRESHOLD,
    ) -> None:
        self._rule = rule_classifier or RuleClassifier()
        self._llm = llm_classifier
        self._threshold = fast_threshold

    async def classify(self, text: str) -> tuple[IntentResult, list[Entity], SentimentLabel, str]:
        """执行双通道分类

        Args:
            text: 用户输入文本

        Returns:
            (IntentResult, 实体列表, 情感标签, 分类来源 "rule"|"llm"|"fallback")
        """
        # Fast Path
        rule_result = self._rule.classify(text)
        if rule_result.primary_confidence >= self._threshold:
            logger.debug(
                "Fast Path 命中: intent=%s, confidence=%.2f",
                rule_result.primary_intent.value,
                rule_result.primary_confidence,
            )
            return rule_result, [], SentimentLabel.NEUTRAL, "rule"

        # Slow Path
        if self._llm is None:
            logger.debug("Slow Path 不可用，使用 Fast Path 低置信度结果")
            return rule_result, [], SentimentLabel.NEUTRAL, "fallback"

        logger.debug(
            "Fast Path 置信度不足 (%.2f < %.2f)，进入 Slow Path",
            rule_result.primary_confidence,
            self._threshold,
        )

        llm_result, entities, sentiment = await self._llm.classify(text)

        # LLM 结果置信度也很低时，标记来源为 fallback
        source = "llm" if llm_result.primary_confidence >= 0.3 else "fallback"
        return llm_result, entities, sentiment, source


def get_domain(intent: IntentLabel) -> str:
    """获取意图所属域（用于 supervisor 路由）"""
    return INTENT_DOMAINS.get(intent, "fallback")


# ── 解析辅助函数 ──


def _parse_intent(raw: str) -> IntentLabel:
    """将 LLM 输出的意图字符串转为 IntentLabel 枚举"""
    try:
        return IntentLabel(raw)
    except ValueError:
        logger.debug("LLM 输出未知意图: %s", raw)
        return IntentLabel.FAQ


def _parse_entities(raw_entities: list[dict[str, Any]]) -> list[Entity]:
    """将 LLM 输出的实体列表转为 Entity 模型"""
    entities: list[Entity] = []
    for e in raw_entities:
        try:
            entities.append(Entity(
                entity_type=e.get("entity_type", "unknown"),
                value=e.get("value", ""),
                confidence=e.get("confidence", 0.7),
            ))
        except Exception:
            logger.debug("实体解析失败: %s", e)
    return entities


def _parse_sentiment(raw: str) -> SentimentLabel:
    """将 LLM 输出的情感字符串转为 SentimentLabel 枚举"""
    try:
        return SentimentLabel(raw)
    except ValueError:
        return SentimentLabel.NEUTRAL
