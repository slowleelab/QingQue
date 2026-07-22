"""槽位填充追踪器

银行客服场景的对话槽位状态机。追踪"已收集什么信息、还缺什么"，
注入 system prompt 引导 Bot 有序追问。

设计原则:
- 规则驱动（不调 LLM），确定性，0ms
- 槽位定义与意图绑定，不同意图有不同的必填槽位
- 与 SessionState.entity_pool 互补：实体池存储已抽取的 KV，
  槽位追踪管理"需要收集但尚未收集"的缺口
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from smartcs.shared.models import IntentLabel

# ── 槽位定义 ──


@dataclass
class SlotDef:
    """槽位定义"""
    name: str            # 槽位名（如 "amount"/"period"）
    label: str           # 中文标签（如 "金额"/"期数"）
    required: bool = True
    ask_prompt: str = ""  # 追问话术模板


# 各意图的槽位定义
_INTENT_SLOTS: dict[IntentLabel, list[SlotDef]] = {
    IntentLabel.INSTALLMENT_INQUIRY: [
        SlotDef("amount", "分期金额", True, "请问您想分期的金额是多少？"),
        SlotDef("period", "分期期数", True, "您希望分几期？"),
    ],
    IntentLabel.BILL_QUERY: [
        SlotDef("period", "账单周期", False, "请问您要查哪个月的账单？"),
    ],
    IntentLabel.LIMIT_QUERY: [
        SlotDef("card_type", "卡种", False, "请问您持有的是哪种卡？"),
    ],
    IntentLabel.CARD_LOSS: [
        SlotDef("card_tail", "卡号后四位", True, "请提供您信用卡的后四位以便验证身份"),
        SlotDef("phone_number", "预留手机号", False, "请确认您的预留手机号"),
    ],
    IntentLabel.COMPLAINT: [
        SlotDef("issue_detail", "问题详情", True, "请详细描述您遇到的问题"),
    ],
    IntentLabel.TRANSACTION_QUERY: [
        SlotDef("period", "查询时段", False, "请问您要查哪个时间段的交易？"),
        SlotDef("amount", "交易金额", False, "请问您要查的是哪笔金额的交易？"),
    ],
    IntentLabel.REWARD_QUERY: [
        SlotDef("card_type", "卡种", False, "请问您持有的是哪种卡？"),
    ],
}

# Fallback: 通用槽位（FAQ/chitchat 无强制槽位）
_FALLBACK_SLOTS: list[SlotDef] = []

# 已知实体类型 → 槽位名映射（从 entity_pool 自动填充槽位）
_ENTITY_TO_SLOT: dict[str, str] = {
    "amount": "amount",
    "CARD_NUMBER": "card_number",
    "card_tail": "card_tail",
    "PHONE": "phone_number",
    "period": "period",
    "DATE": "period",
    "card_type": "card_type",
}


# ── 追踪器 ──


@dataclass
class SlotState:
    """单个槽位的当前状态"""

    name: str
    label: str
    required: bool
    ask_prompt: str
    filled: bool = False
    value: str | None = None


@dataclass
class SlotTracker:
    """槽位填充追踪器（per-session）"""

    intent: str = "faq"
    slots: list[SlotState] = field(default_factory=list)

    @classmethod
    def for_intent(cls, intent: IntentLabel | str) -> SlotTracker:
        """按意图创建槽位追踪器"""
        if isinstance(intent, str):
            try:
                intent = IntentLabel(intent)
            except ValueError:
                intent = IntentLabel.FAQ

        slot_defs = _INTENT_SLOTS.get(intent, _FALLBACK_SLOTS)
        return cls(
            intent=intent.value,
            slots=[
                SlotState(name=sd.name, label=sd.label, required=sd.required, ask_prompt=sd.ask_prompt)
                for sd in slot_defs
            ],
        )

    def fill_from_entities(self, entities: list[dict[str, str]]) -> None:
        """从已抽取实体池自动填充槽位"""
        for entity in entities:
            etype = entity.get("entity_type", "")
            value = entity.get("value", "")
            if not value:
                continue
            slot_name = _ENTITY_TO_SLOT.get(etype)
            if slot_name:
                self._mark_filled(slot_name, value)

    def fill_from_message(self, key: str, value: str) -> None:
        """手动标记槽位已填充"""
        self._mark_filled(key, value)

    def _mark_filled(self, slot_name: str, value: str) -> None:
        for s in self.slots:
            if s.name == slot_name and not s.filled:
                s.filled = True
                s.value = value
                return

    @property
    def missing_required(self) -> list[SlotState]:
        """尚未填充的必填槽位"""
        return [s for s in self.slots if s.required and not s.filled]

    @property
    def all_required_filled(self) -> bool:
        return len(self.missing_required) == 0

    @property
    def has_slots(self) -> bool:
        """是否有槽位定义（FAQ 等意图无槽位）"""
        return len(self.slots) > 0

    def build_prompt(self) -> str:
        """生成槽位状态 prompt 段（注入 system prompt）

        未填充的必填槽位 → 引导 Bot 追问
        已填充的槽位 → 告知 Bot 无需重复收集
        """
        if not self.slots:
            return ""

        lines: list[str] = ["[槽位状态]"]
        filled = [s for s in self.slots if s.filled]
        missing = self.missing_required

        if filled:
            items = ", ".join(f"{s.label}={s.value}" for s in filled)
            lines.append(f"已收集: {items}")

        if missing:
            items = ", ".join(s.label for s in missing)
            lines.append(f"待收集: {items}")
            # 追加追问提示
            if missing:
                hints = "；".join(f"需要收集{s.label}时请追问: {s.ask_prompt}" for s in missing[:2])
                lines.append(f"追问提示: {hints}")

        if not filled and not missing:
            lines.append("（无所需信息，可直接回答）")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "slots": [{"name": s.name, "label": s.label, "required": s.required, "filled": s.filled, "value": s.value} for s in self.slots],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SlotTracker:
        tracker = cls(intent=data.get("intent", "faq"))
        for sd in data.get("slots", []):
            tracker.slots.append(
                SlotState(name=sd["name"], label=sd["label"], required=sd["required"],
                          ask_prompt=sd.get("ask_prompt", ""), filled=sd.get("filled", False),
                          value=sd.get("value"))
            )
        return tracker
