"""全局仲裁器

对应设计文档 §3.5 仲裁与输出层 + §4.2 安全与隐私。
优先级融合展示规则 + 全局合规校验 (PII 脱敏 + 合规短语过滤)。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from smartcs.workflows.shared import ExecutorOutput, OrchestrationResult

logger = logging.getLogger(__name__)

# PII 脱敏规则（对应文档 §4.2）
# 顺序: 身份证 > 银行卡 > 手机号，长模式优先避免短模式误匹配
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 身份证号: 18位，前后不能有更多数字
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[IDCARD]"),
    # 银行卡号: 16-19位数字，前后不能有更多数字
    (re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "[BANKCARD]"),
    # 手机号: 1[3-9]开头的11位数字，前后不能有更多数字
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[PHONE]"),
    # 中文姓名: 在"客户"或"姓名"后跟的2-4个汉字，保留前缀替换姓名
    # 使用非贪婪量词 + 前瞻断言，避免贪婪匹配到助词/后缀
    (re.compile(r"(客户|姓名[是为：:]?\s*)([\u4e00-\u9fa5]{2,4}?)(?=[^\u4e00-\u9fa5]|的|了|是|在|有|和|与|先生|女士|同志|$)"), r"\1[NAME]"),
]

# 独立姓名后缀脱敏: 2-4个中文字后跟"先生/女士/同志"
_SIMPLIFIED_NAME_PATTERN = re.compile(r"[\u4e00-\u9fa5]{2,4}(?:先生|女士|同志)")

# 合规短语过滤黑名单（对应文档 §3.5 合规短语过滤）
_COMPLIANCE_BLOCKED_PHRASES: list[str] = [
    "保证收益",
    "稳赚不赔",
    "零风险",
    "绝对安全",
    "保本保息",
]


class GlobalArbitrator:
    """全局仲裁器

    优先级融合规则 (§3.5):
    - 风控 BLOCK: 主卡片=风控拦截, 营销=不展示
    - 风控 WARN: 主卡片=服务, 风险标记=徽章, 营销=降级小卡片
    - 风控 PASS: 主卡片=服务, 辅助=营销(标准)

    全局合规校验 (§3.5):
    - PII 脱敏 + 合规短语过滤
    """

    async def arbitrate(
        self,
        results: dict[str, ExecutorOutput],
        state_snapshot: dict[str, Any] | None = None,
    ) -> OrchestrationResult:
        """仲裁融合"""
        risk = results.get("risk")
        ai = results.get("ai_service")
        mkt = results.get("marketing")
        risk_action = risk.risk_action if risk else "PASS"

        if risk_action == "BLOCK":
            result = self._fuse_risk_blocked(risk, ai)
        elif risk_action == "WARN":
            result = self._fuse_risk_warn(risk, ai, mkt)
        else:
            result = self._fuse_risk_pass(ai, mkt)

        # 全局合规校验: PII 脱敏
        result.primary_card = self._mask_pii_recursive(result.primary_card)
        result.risk_badge = self._mask_pii_recursive(result.risk_badge) if result.risk_badge else None
        result.marketing_slot = self._mask_pii_recursive(result.marketing_slot) if result.marketing_slot else None

        # 全局合规校验: 合规短语过滤
        result.primary_card = self._filter_compliance_recursive(result.primary_card)

        return result

    def _fuse_risk_blocked(
        self, risk: ExecutorOutput | None, ai: ExecutorOutput | None
    ) -> OrchestrationResult:
        """风控 BLOCK 融合"""
        primary = {
            "type": "risk_block",
            "content": risk.ui_schema if risk else {},
        }
        return OrchestrationResult(
            primary_card=primary,
            risk_badge=None,
            marketing_slot=None,
            fusion_type="risk_blocked",
        )

    def _fuse_risk_warn(
        self, risk: ExecutorOutput | None, ai: ExecutorOutput | None, mkt: ExecutorOutput | None
    ) -> OrchestrationResult:
        """风控 WARN 融合"""
        primary = {"type": "service_answer", "content": ai.ui_schema if ai else {}}
        risk_badge = {
            "type": "risk_badge",
            "alerts": risk.ui_schema.get("alerts", []) if risk else [],
        }
        marketing_slot = None
        if mkt and mkt.ui_schema.get("marketing_cards"):
            marketing_slot = {"type": "marketing_small", "content": mkt.ui_schema}
        return OrchestrationResult(
            primary_card=primary,
            risk_badge=risk_badge,
            marketing_slot=marketing_slot,
            fusion_type="service_risk_warn",
        )

    def _fuse_risk_pass(
        self, ai: ExecutorOutput | None, mkt: ExecutorOutput | None
    ) -> OrchestrationResult:
        """风控 PASS 融合"""
        primary = {"type": "service_answer", "content": ai.ui_schema if ai else {}}
        marketing_slot = None
        if mkt and mkt.ui_schema.get("marketing_cards"):
            marketing_slot = {"type": "marketing_standard", "content": mkt.ui_schema}
        fusion = "service_marketing" if marketing_slot else "service_only"
        return OrchestrationResult(
            primary_card=primary,
            risk_badge=None,
            marketing_slot=marketing_slot,
            fusion_type=fusion,
        )

    def mask_pii(self, text: str) -> str:
        """PII 脱敏（公开方法，供外部调用）"""
        for pattern, replacement in _PII_PATTERNS:
            text = pattern.sub(replacement, text)
        # 姓名后缀脱敏
        text = _SIMPLIFIED_NAME_PATTERN.sub("[NAME]", text)
        return text

    def _mask_pii_recursive(self, obj: Any) -> Any:
        """递归脱敏"""
        if isinstance(obj, str):
            return self.mask_pii(obj)
        elif isinstance(obj, dict):
            return {k: self._mask_pii_recursive(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._mask_pii_recursive(item) for item in obj]
        return obj

    def filter_compliance(self, text: str) -> str:
        """合规短语过滤（公开方法，供外部调用）"""
        for phrase in _COMPLIANCE_BLOCKED_PHRASES:
            text = text.replace(phrase, "[已过滤]")
        return text

    def _filter_compliance_recursive(self, obj: Any) -> Any:
        """递归合规短语过滤"""
        if isinstance(obj, str):
            return self.filter_compliance(obj)
        elif isinstance(obj, dict):
            return {k: self._filter_compliance_recursive(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._filter_compliance_recursive(item) for item in obj]
        return obj
