"""质检告警引擎

合规检查 + 情绪检测 + 趋势分析 + 告警聚合。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from smartcs.shared.models import AlertLevel, SentimentLabel

logger = logging.getLogger(__name__)

# ── 种子合规规则 ──

_SEED_RULES: list[dict[str, Any]] = [
    {
        "rule_id": "R-COMP-001",
        "category": "compliance",
        "level": "critical",
        "pattern": r"(套现|提额.*包过|内部渠道|免审核|无视征信)",
        "message": "检测到疑似违规承诺或套现引导",
        "suggestion": "请立即停止并警告客户此类行为违反监管规定",
        "priority": 10,
    },
    {
        "rule_id": "R-COMP-002",
        "category": "compliance",
        "level": "critical",
        "pattern": r"(1[3-9]\d{9}|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{4})",
        "message": "对话中疑似泄露客户身份证号",
        "suggestion": "请避免在对话中传输完整身份证号，使用脱敏格式",
        "priority": 9,
    },
    {
        "rule_id": "R-COMP-003",
        "category": "compliance",
        "level": "warning",
        "pattern": r"(保证|承诺|100%|绝对|肯定.*批|必须.*过)",
        "message": "检测到过度承诺用语",
        "suggestion": "请使用客观表述，避免对审批结果做保证性承诺",
        "priority": 7,
    },
    {
        "rule_id": "R-COMP-004",
        "category": "compliance",
        "level": "warning",
        "pattern": r"(骂人|傻[逼Xx]|fuck|shit|垃圾银行|骗子)",
        "message": "检测到不文明用语",
        "suggestion": "请保持专业态度，必要时转交主管处理",
        "priority": 6,
    },
    {
        "rule_id": "R-COMP-005",
        "category": "compliance",
        "level": "warning",
        "pattern": r"(1[3-9]\d)\d{4}(\d{4})",
        "message": "对话中可能存在未脱敏的手机号",
        "suggestion": "请确认手机号已脱敏（如：138****5678）",
        "priority": 8,
    },
    {
        "rule_id": "R-COMP-006",
        "category": "compliance",
        "level": "info",
        "pattern": r"(密码|pin|CVV|cvv|有效期.*卡)",
        "message": "对话涉及敏感卡片信息",
        "suggestion": "请勿在对话中记录或传输 CVV、密码等敏感信息",
        "priority": 8,
    },
]


class AlertEngine:
    """质检告警引擎"""

    def __init__(self) -> None:
        self._rules: list[dict[str, Any]] = []

    def load_from_memory(self, rules: list[dict[str, Any]] | None = None) -> None:
        self._rules = list(rules or _SEED_RULES)
        logger.info("从内存加载 %d 条质检规则", len(self._rules))

    async def load_from_db(self, db_session) -> None:
        from sqlalchemy import select

        from smartcs.shared.orm_models import AlertRule, ScriptStatus

        result = await db_session.execute(select(AlertRule).where(AlertRule.status == ScriptStatus.ACTIVE))
        rows = result.scalars().all()
        self._rules = [
            {
                "rule_id": r.rule_id,
                "category": r.category.value.lower(),
                "level": r.level.value.lower(),
                "pattern": r.pattern,
                "message": r.message,
                "suggestion": r.suggestion,
                "priority": r.priority,
            }
            for r in rows
        ]
        logger.info("从数据库加载 %d 条质检规则", len(self._rules))

    def check_compliance(self, text: str) -> list[dict[str, Any]]:
        """合规检查：正则匹配告警规则"""
        alerts = []
        for rule in self._rules:
            if rule["category"] == "compliance":
                try:
                    if re.search(rule["pattern"], text, re.IGNORECASE):
                        alerts.append(
                            {
                                "level": rule["level"],
                                "category": "compliance",
                                "message": rule["message"],
                                "suggestion": rule["suggestion"],
                            }
                        )
                except re.error as e:
                    logger.warning("规则 %s 正则错误: %s", rule["rule_id"], e)
        return alerts

    def check_sentiment(self, sentiment: SentimentLabel) -> list[dict[str, Any]]:
        """情绪检测：负面/愤怒触发告警"""
        if sentiment == SentimentLabel.ANGRY:
            return [
                {
                    "level": AlertLevel.CRITICAL.value,
                    "category": "emotion",
                    "message": "客户情绪激动，请使用安抚话术",
                    "suggestion": "先道歉安抚，表示理解和重视，承诺快速处理",
                }
            ]
        if sentiment == SentimentLabel.NEGATIVE:
            return [
                {
                    "level": AlertLevel.WARNING.value,
                    "category": "emotion",
                    "message": "客户情绪较低落/不满",
                    "suggestion": "表达理解和同理心，积极解决问题",
                }
            ]
        return []

    def check_sentiment_trend(self, history: list[SentimentLabel], window: int = 3) -> list[dict[str, Any]]:
        """情绪趋势分析：连续 N 轮负面/愤怒 → 升级告警"""
        if len(history) < window:
            return []
        recent = history[-window:]
        negative_count = sum(1 for s in recent if s in (SentimentLabel.NEGATIVE, SentimentLabel.ANGRY))
        if negative_count >= window:
            return [
                {
                    "level": AlertLevel.CRITICAL.value,
                    "category": "emotion",
                    "message": f"客户连续 {window} 轮情绪不佳，建议升级处理",
                    "suggestion": "转交主管或启动投诉处理流程",
                }
            ]
        return []

    def check_all(
        self,
        text: str,
        sentiment: SentimentLabel,
        sentiment_history: list[SentimentLabel],
        trend_window: int = 3,
    ) -> list[dict[str, Any]]:
        """全量检查：合规 + 情绪 + 趋势"""
        alerts = []
        alerts.extend(self.check_compliance(text))
        alerts.extend(self.check_sentiment(sentiment))
        alerts.extend(self.check_sentiment_trend(sentiment_history, trend_window))
        return alerts
