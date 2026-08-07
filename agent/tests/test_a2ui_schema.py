"""A2UI 富响应 Schema 单元测试 (a2ui_schema.py)"""

from __future__ import annotations

from lumio.shared.a2ui_schema import (
    Attachment,
    Card,
    CardType,
    QuickReply,
    UIRichResponse,
    bill_summary_card,
    complaint_ticket_card,
    credit_adjustment_card,
    installment_plan_card,
    points_balance_card,
)

# ── 基础模型 ──


def test_card_model_defaults():
    """Card 缺省字段为空容器"""
    card = Card(type=CardType.BILL_SUMMARY, title="本期账单")
    assert card.fields == {}
    assert card.actions == []


def test_card_serialization():
    """Card 可序列化为 dict"""
    card = Card(type=CardType.BILL_SUMMARY, title="t", fields={"a": 1})
    data = card.model_dump()
    assert data["type"] == "bill_summary"
    assert data["fields"] == {"a": 1}


def test_quick_reply_model():
    """QuickReply 可选字段"""
    qr = QuickReply(label="是", value="yes")
    assert qr.intent_hint is None
    qr2 = QuickReply(label="是", value="yes", intent_hint="CONFIRM")
    assert qr2.intent_hint == "CONFIRM"


def test_attachment_model():
    """Attachment 可选 size_bytes"""
    att = Attachment(type="pdf", url="http://x/b.pdf", filename="b.pdf")
    assert att.size_bytes is None
    att2 = Attachment(type="pdf", url="u", filename="f", size_bytes=1024)
    assert att2.size_bytes == 1024


def test_ui_rich_response_defaults():
    """UIRichResponse 全字段缺省"""
    resp = UIRichResponse(text="hi")
    assert resp.cards == []
    assert resp.quick_replies == []
    assert resp.attachments == []
    assert resp.thinking is None
    assert resp.metadata == {}


def test_ui_rich_response_full():
    """UIRichResponse 组装完整富响应"""
    resp = UIRichResponse(
        text="查询结果",
        cards=[Card(type=CardType.POINTS_BALANCE, title="积分")],
        quick_replies=[QuickReply(label="转人工", value="转人工")],
        attachments=[Attachment(type="pdf", url="u", filename="f")],
        thinking="正在查询...",
        metadata={"session_id": "s1"},
    )
    data = resp.model_dump()
    assert data["text"] == "查询结果"
    assert len(data["cards"]) == 1
    assert data["metadata"]["session_id"] == "s1"


# ── 卡片工厂 ──


def test_bill_summary_card():
    """账单汇总卡片: 金额格式化 + 3 个动作"""
    card = bill_summary_card(bill_amount=1234.5, due_date="2026-09-05", min_payment=100.0)
    assert card.type == CardType.BILL_SUMMARY
    assert card.title == "本期账单"
    assert card.fields["bill_amount"] == "¥1234.50"
    assert card.fields["due_date"] == "2026-09-05"
    assert card.fields["min_payment"] == "¥100.00"
    assert card.fields["statement_date"] == ""  # 缺省空
    assert len(card.actions) == 3
    assert card.actions[0]["intent_hint"] == "REPAY_NOW"


def test_credit_adjustment_card():
    """调额结果卡片: 新旧额度 + 增幅"""
    card = credit_adjustment_card(new_limit=80000.0, old_limit=50000.0, effective_date="2026-08-10")
    assert card.type == CardType.CREDIT_ADJUSTMENT
    assert card.fields["new_limit"] == "¥80,000"
    assert card.fields["old_limit"] == "¥50,000"
    assert card.fields["increase"] == "¥30,000"
    assert card.fields["approval_days"] == "3 个工作日"  # 默认 3 天


def test_installment_plan_card():
    """分期方案卡片: 每个方案一个动作"""
    plans = [
        {"periods": 6, "monthly_rate": "0.75%"},
        {"periods": 12, "monthly_rate": "0.60%"},
    ]
    card = installment_plan_card(total_amount=5000.0, plans=plans)
    assert card.type == CardType.INSTALLMENT_PLAN
    assert card.fields["total_amount"] == "¥5000.00"
    assert len(card.actions) == 2
    assert card.actions[1]["intent_hint"] == "INSTALLMENT_12"


def test_complaint_ticket_card():
    """投诉工单卡片"""
    card = complaint_ticket_card(ticket_id="T12345")
    assert card.type == CardType.COMPLAINT_TICKET
    assert card.fields["ticket_id"] == "T12345"
    assert card.fields["eta_hours"] == "48 小时内"  # 默认 48h
    assert card.fields["contact_phone"] == "955xx"
    assert len(card.actions) == 2


def test_points_balance_card():
    """积分余额卡片: 千分位格式化"""
    card = points_balance_card(total_points=123456)
    assert card.type == CardType.POINTS_BALANCE
    assert card.fields["total_points"] == "123,456"
    assert card.fields["expiring_soon"] == "0"
    assert card.fields["expiry_date"] == ""
    card2 = points_balance_card(total_points=100, expiring_soon=50, expiry_date="2026-12-31")
    assert card2.fields["expiring_soon"] == "50"
    assert card2.fields["expiry_date"] == "2026-12-31"
