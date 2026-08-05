"""A2UI (Agent to UI) 富响应 Schema.

为客服 Bot 端提供结构化 UI 渲染能力, 让客户端能渲染:
- 文本主体
- 信息卡片 (调额结果/账单/额度/投诉工单)
- 快速回复按钮 (是/否/转人工)
- 附件 (PDF账单)

替代纯文本答复, 提升客户体验.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CardType(str, Enum):
    """卡片类型."""

    BILL_SUMMARY = "bill_summary"  # 账单汇总
    CREDIT_ADJUSTMENT = "credit_adjustment"  # 调额结果
    POINTS_BALANCE = "points_balance"  # 积分余额
    INSTALLMENT_PLAN = "installment_plan"  # 分期方案
    COMPLAINT_TICKET = "complaint_ticket"  # 投诉工单
    CARD_INFO = "card_info"  # 卡片信息
    RATE_TABLE = "rate_table"  # 利率/费率表


class Card(BaseModel):
    """通用卡片."""

    type: CardType
    title: str
    fields: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)  # 按钮 actions


class QuickReply(BaseModel):
    """快速回复按钮."""

    label: str  # 显示文本
    value: str  # 实际发送值
    intent_hint: str | None = None  # 提示意图 (前端可用)


class Attachment(BaseModel):
    """附件 (PDF/图片)."""

    type: str  # "pdf" / "image"
    url: str
    filename: str
    size_bytes: int | None = None


class UIRichResponse(BaseModel):
    """完整富响应 (Bot 端 WS 推送格式)."""

    text: str = ""  # 主文本
    cards: list[Card] = Field(default_factory=list)
    quick_replies: list[QuickReply] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    thinking: str | None = None  # "正在查询..."
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── 卡片工厂 ──

def bill_summary_card(
    bill_amount: float,
    due_date: str,
    min_payment: float,
    statement_date: str | None = None,
) -> Card:
    """账单汇总卡片."""
    return Card(
        type=CardType.BILL_SUMMARY,
        title="本期账单",
        fields={
            "bill_amount": f"¥{bill_amount:.2f}",
            "due_date": due_date,
            "min_payment": f"¥{min_payment:.2f}",
            "statement_date": statement_date or "",
        },
        actions=[
            {"label": "立即还款", "intent_hint": "REPAY_NOW"},
            {"label": "申请分期", "intent_hint": "BILL_INSTALLMENT"},
            {"label": "查看明细", "intent_hint": "BILL_DETAIL"},
        ],
    )


def credit_adjustment_card(
    new_limit: float,
    old_limit: float,
    effective_date: str,
    approval_days: int = 3,
) -> Card:
    """调额结果卡片."""
    return Card(
        type=CardType.CREDIT_ADJUSTMENT,
        title="额度调整结果",
        fields={
            "new_limit": f"¥{new_limit:,.0f}",
            "old_limit": f"¥{old_limit:,.0f}",
            "increase": f"¥{new_limit - old_limit:,.0f}",
            "effective_date": effective_date,
            "approval_days": f"{approval_days} 个工作日",
        },
    )


def installment_plan_card(
    total_amount: float,
    plans: list[dict[str, Any]],
) -> Card:
    """分期方案卡片."""
    return Card(
        type=CardType.INSTALLMENT_PLAN,
        title="分期方案",
        fields={
            "total_amount": f"¥{total_amount:.2f}",
            "plans": plans,  # [{"periods": 6, "monthly_rate": "0.75%", "monthly_payment": ...}]
        },
        actions=[
            {"label": f"选 {p['periods']} 期", "intent_hint": f"INSTALLMENT_{p['periods']}"}
            for p in plans
        ],
    )


def complaint_ticket_card(
    ticket_id: str,
    eta_hours: int = 48,
    contact_phone: str = "955xx",
) -> Card:
    """投诉工单卡片."""
    return Card(
        type=CardType.COMPLAINT_TICKET,
        title="投诉已受理",
        fields={
            "ticket_id": ticket_id,
            "eta_hours": f"{eta_hours} 小时内",
            "contact_phone": contact_phone,
        },
        actions=[
            {"label": "查询进度", "intent_hint": "COMPLAINT_PROGRESS"},
            {"label": "升级处理", "intent_hint": "COMPLAINT_ESCALATE"},
        ],
    )


def points_balance_card(
    total_points: int,
    expiring_soon: int = 0,
    expiry_date: str | None = None,
) -> Card:
    """积分余额卡片."""
    return Card(
        type=CardType.POINTS_BALANCE,
        title="积分余额",
        fields={
            "total_points": f"{total_points:,}",
            "expiring_soon": f"{expiring_soon:,}" if expiring_soon else "0",
            "expiry_date": expiry_date or "",
        },
        actions=[
            {"label": "兑换商城", "intent_hint": "POINTS_MALL"},
            {"label": "兑换明细", "intent_hint": "POINTS_DETAIL"},
        ],
    )
