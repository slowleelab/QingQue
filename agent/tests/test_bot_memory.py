"""Bot 记忆增强单元测试

覆盖三项记忆优化：
- _estimate_tokens: 字符类感知 token 估算
- _is_important: 关键轮次标记
- SlotTracker: 槽位填充状态机
"""

from __future__ import annotations

from smartcs.services.bot.bot_agent import _estimate_tokens, _is_important
from smartcs.services.bot.slot_tracker import SlotTracker
from smartcs.shared.models import Entity, IntentLabel


class TestEstimateTokens:
    """Token 估算测试"""

    def test_chinese_text(self) -> None:
        """纯中文: 约 0.55 tokens/char"""
        text = "信用卡年费怎么减免呢"
        est = _estimate_tokens(text)
        # 10 chars * 0.55 + 4 = ~9.5
        assert 6 < est < 14

    def test_english_text(self) -> None:
        """纯英文: 约 0.3 tokens/char"""
        text = "How can I waive the annual fee"
        est = _estimate_tokens(text)
        # 32 chars * 0.3 + 4 = ~13.6
        assert 8 < est < 18

    def test_mixed_chinese_english(self) -> None:
        """中英混合精确度优于 len*2"""
        text = "我的信用卡 credit card 年费 annual fee 怎么减免"
        est = _estimate_tokens(text)
        old = len(text) * 2 + 20
        # 新估算法应更保守（CJK 0.55 vs 2.0，Latin 0.3 vs 2.0）
        assert est < old, f"新估算法应比 len*2+20={old} 更准确（{est}）"

    def test_short_text(self) -> None:
        """短文本有 +4 保底"""
        assert _estimate_tokens("你好") >= 4

    def test_empty(self) -> None:
        assert _estimate_tokens("") == 4


class TestImportantTurns:
    """关键轮次标记测试"""

    def test_complaint_is_important(self) -> None:
        assert _is_important("我要投诉你们")

    def test_transfer_is_important(self) -> None:
        assert _is_important("转人工客服")

    def test_promise_is_important(self) -> None:
        assert _is_important("我保证这件事一定能解决")

    def test_fraud_is_important(self) -> None:
        assert _is_important("我的卡被盗刷了")

    def test_freeze_is_important(self) -> None:
        assert _is_important("请帮我冻结账户")

    def test_regulatory_is_important(self) -> None:
        assert _is_important("我要向银保监会举报")

    def test_normal_query_not_important(self) -> None:
        assert _is_important("信用卡年费怎么减免") is False

    def test_greeting_not_important(self) -> None:
        assert _is_important("你好") is False


class TestSlotTracker:
    """槽位追踪器测试"""

    def test_installment_has_amount_and_period(self) -> None:
        tracker = SlotTracker.for_intent(IntentLabel.INSTALLMENT_INQUIRY)
        assert len(tracker.slots) == 2
        names = {s.name for s in tracker.slots}
        assert "amount" in names
        assert "period" in names

    def test_card_loss_requires_card_tail(self) -> None:
        tracker = SlotTracker.for_intent(IntentLabel.CARD_LOSS)
        assert tracker.missing_required
        assert any(s.name == "card_tail" and s.required for s in tracker.missing_required)

    def test_faq_has_no_slots(self) -> None:
        tracker = SlotTracker.for_intent(IntentLabel.FAQ)
        assert not tracker.has_slots

    def test_unknown_intent_fallback_no_slots(self) -> None:
        tracker = SlotTracker.for_intent("nonexistent")
        assert not tracker.has_slots

    def test_fill_from_entities(self) -> None:
        tracker = SlotTracker.for_intent(IntentLabel.INSTALLMENT_INQUIRY)
        entities = [
            {"entity_type": "amount", "value": "5000"},
            {"entity_type": "DATE", "value": "2026-01"},
        ]
        tracker.fill_from_entities(entities)
        # amount 槽位应被填充
        amount_slot = next(s for s in tracker.slots if s.name == "amount")
        assert amount_slot.filled
        assert amount_slot.value == "5000"
        # period 通过 DATE 映射也应填充
        period_slot = next(s for s in tracker.slots if s.name == "period")
        assert period_slot.filled

    def test_missing_required_after_partial_fill(self) -> None:
        tracker = SlotTracker.for_intent(IntentLabel.INSTALLMENT_INQUIRY)
        tracker.fill_from_entities([{"entity_type": "amount", "value": "5000"}])
        missing = tracker.missing_required
        assert len(missing) == 1
        assert missing[0].name == "period"

    def test_all_required_filled(self) -> None:
        tracker = SlotTracker.for_intent(IntentLabel.INSTALLMENT_INQUIRY)
        tracker.fill_from_entities([
            {"entity_type": "amount", "value": "5000"},
            {"entity_type": "period", "value": "12"},
        ])
        assert tracker.all_required_filled

    def test_build_prompt_shows_missing(self) -> None:
        tracker = SlotTracker.for_intent(IntentLabel.CARD_LOSS)
        prompt = tracker.build_prompt()
        assert "槽位状态" in prompt
        assert "待收集" in prompt
        assert "后四位" in prompt

    def test_build_prompt_shows_filled(self) -> None:
        tracker = SlotTracker.for_intent(IntentLabel.INSTALLMENT_INQUIRY)
        tracker.fill_from_entities([{"entity_type": "amount", "value": "5000"}])
        prompt = tracker.build_prompt()
        assert "已收集" in prompt
        assert "5000" in prompt

    def test_roundtrip_serialization(self) -> None:
        tracker = SlotTracker.for_intent(IntentLabel.INSTALLMENT_INQUIRY)
        tracker.fill_from_entities([{"entity_type": "amount", "value": "5000"}])
        data = tracker.to_dict()
        restored = SlotTracker.from_dict(data)
        assert restored.intent == tracker.intent
        assert restored.missing_required[0].name == "period"

    def test_intent_switch_resets_tracker(self) -> None:
        """切换意图时应重新创建 tracker（from_dict 检查 intent 不匹配时调用方应新建）"""
        tracker1 = SlotTracker.for_intent(IntentLabel.INSTALLMENT_INQUIRY)
        tracker1.fill_from_entities([{"entity_type": "amount", "value": "5000"}])
        # 模拟意图切换：from_dict 读回旧意图数据，调用方应新建
        old_data = tracker1.to_dict()
        # 新意图不是 installment → 调用方不应使用旧 tracker
        assert old_data["intent"] == "installment_inquiry"
        
        # 新建 card_loss tracker 应独立
        tracker2 = SlotTracker.for_intent(IntentLabel.CARD_LOSS)
        assert tracker2.missing_required
        assert any(s.name == "card_tail" for s in tracker2.missing_required)
