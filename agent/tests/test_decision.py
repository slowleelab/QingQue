"""展示决策模块单元测试"""

from __future__ import annotations

import time

from lumio.services.common.decision import (
    FeedbackAction,
    PushTracker,
    Scene,
    detect_scene,
    should_show,
)


class TestDetectScene:
    """场景检测测试"""

    def test_urgent_loss(self) -> None:
        assert detect_scene("我卡丢了") == Scene.URGENT

    def test_urgent_stolen(self) -> None:
        assert detect_scene("卡被盗刷了") == Scene.URGENT

    def test_urgent_complaint(self) -> None:
        assert detect_scene("我要投诉") == Scene.URGENT

    def test_urgent_freeze(self) -> None:
        assert detect_scene("请帮我冻结账户") == Scene.URGENT

    def test_sales_apply(self) -> None:
        assert detect_scene("我想办卡") == Scene.SALES

    def test_sales_limit(self) -> None:
        assert detect_scene("能提额吗") == Scene.SALES

    def test_sales_installment(self) -> None:
        assert detect_scene("分期怎么算") == Scene.SALES

    def test_inquiry_bill(self) -> None:
        assert detect_scene("查一下账单") == Scene.INQUIRY

    def test_inquiry_points(self) -> None:
        assert detect_scene("积分还有多少") == Scene.INQUIRY

    def test_inquiry_balance(self) -> None:
        assert detect_scene("余额查询") == Scene.INQUIRY

    def test_general(self) -> None:
        assert detect_scene("今天天气不错") == Scene.GENERAL

    def test_general_hello(self) -> None:
        assert detect_scene("你好") == Scene.GENERAL

    def test_case_insensitive(self) -> None:
        assert detect_scene("我要挂失") == Scene.URGENT

    # ── intent 融合 ──

    def test_intent_card_loss_forces_urgent(self) -> None:
        """intent=card_loss 直接判定 URGENT，即使消息无关键词"""
        assert detect_scene("你好", intent="card_loss") == Scene.URGENT

    def test_intent_complaint_forces_urgent(self) -> None:
        """intent=complaint 直接判定 URGENT"""
        assert detect_scene("没事", intent="complaint") == Scene.URGENT

    def test_intent_fallback_when_no_keyword(self) -> None:
        """关键词未命中时，intent 映射作为回退"""
        assert detect_scene("嗯哼", intent="bill_query") == Scene.INQUIRY
        assert detect_scene("嗯哼", intent="installment_inquiry") == Scene.SALES

    def test_intent_none_falls_to_general(self) -> None:
        """无关键词且无 intent -> GENERAL"""
        assert detect_scene("嗯哼", intent=None) == Scene.GENERAL

    def test_urgent_keyword_overrides_sales_intent(self) -> None:
        """URGENT 关键词优先于 SALES intent（紧急场景不被营销意图覆盖）"""
        assert detect_scene("我卡丢了", intent="installment_inquiry") == Scene.URGENT

    # ── 否定语义 ──

    def test_negation_suppresses_sales(self) -> None:
        """'我不想分期' 不应判定为 SALES"""
        assert detect_scene("我不想分期") != Scene.SALES

    def test_negation_suppresses_inquiry(self) -> None:
        """'别查账单了' 不应判定为 INQUIRY"""
        assert detect_scene("别查账单了") != Scene.INQUIRY

    def test_negation_not_overreach(self) -> None:
        """否定词距离关键词过远时不误判（'不' 在句首，'办卡' 在句尾）"""
        # 这里'不'和'办卡'距离 > 4，应判定为 SALES
        assert detect_scene("不知道你们能不能办卡") == Scene.SALES

    def test_negation_without_intent_falls_to_general(self) -> None:
        """关键词被否定且无 intent -> GENERAL（'我不想分期' 无 intent 时不判 SALES）"""
        assert detect_scene("我不想分期") == Scene.GENERAL

    def test_negation_with_intent_trusts_classifier(self) -> None:
        """关键词被否定但有 intent -> 信任分类器（intent 是深层判断，否定只是表层词法）"""
        # '我不想分期' + intent=installment_inquiry -> SALES（分类器已判断为分期咨询）
        assert detect_scene("我不想分期", intent="installment_inquiry") == Scene.SALES


class TestShouldShow:
    """展示决策测试"""

    def test_risk_block_always_shows(self) -> None:
        """风控 BLOCK 强制展示"""
        tracker = PushTracker()
        decision = should_show("risk", Scene.GENERAL, tracker, risk_action="BLOCK")
        assert decision.should_show is True
        assert "强制展示" in decision.reason

    def test_risk_warn_always_shows(self) -> None:
        """风控 WARN 强制展示"""
        tracker = PushTracker()
        decision = should_show("risk", Scene.GENERAL, tracker, risk_action="WARN")
        assert decision.should_show is True

    def test_risk_pass_no_show(self) -> None:
        """风控 PASS 无需展示"""
        tracker = PushTracker()
        decision = should_show("risk", Scene.GENERAL, tracker, risk_action="PASS")
        assert decision.should_show is False

    def test_marketing_blocked_in_urgent_scene(self) -> None:
        """紧急场景关闭营销"""
        tracker = PushTracker()
        decision = should_show("marketing", Scene.URGENT, tracker)
        assert decision.should_show is False
        assert "紧急" in decision.reason

    def test_ai_allowed_in_urgent_scene(self) -> None:
        """紧急场景 AI 服务仍可展示"""
        tracker = PushTracker()
        decision = should_show("ai", Scene.URGENT, tracker)
        assert decision.should_show is True

    def test_ai_allowed_on_first_message(self) -> None:
        """首次消息可展示"""
        tracker = PushTracker()
        decision = should_show("ai", Scene.GENERAL, tracker)
        assert decision.should_show is True

    def test_ai_blocked_by_time_interval(self) -> None:
        """时间间隔不足时阻止展示"""
        tracker = PushTracker()
        tracker.last_push_at["ai"] = time.time()  # 刚刚推送过
        decision = should_show("ai", Scene.GENERAL, tracker)
        assert decision.should_show is False
        assert "最小间隔" in decision.reason

    def test_ai_allowed_after_interval(self) -> None:
        """时间间隔足够时允许展示"""
        tracker = PushTracker()
        tracker.last_push_at["ai"] = time.time() - 5.0  # 5 秒前推送
        decision = should_show("ai", Scene.GENERAL, tracker)
        assert decision.should_show is True

    def test_marketing_blocked_after_dismiss(self) -> None:
        """上次被关闭后本轮不展示"""
        tracker = PushTracker()
        tracker.record_feedback("marketing", FeedbackAction.DISMISSED)
        decision = should_show("marketing", Scene.GENERAL, tracker)
        assert decision.should_show is False
        assert "关闭" in decision.reason

    def test_force_show_overrides_all(self) -> None:
        """force_show 覆盖所有规则"""
        tracker = PushTracker()
        tracker.last_push_at["ai"] = time.monotonic()  # 刚刚推送过
        decision = should_show("ai", Scene.GENERAL, tracker, force_show=True)
        assert decision.should_show is True


class TestPushTracker:
    """推送追踪器测试"""

    def test_new_tracker(self) -> None:
        tracker = PushTracker()
        assert tracker.min_interval["ai"] == 3.0
        assert tracker.min_interval["marketing"] == 30.0
        assert tracker.last_push_at == {}
        assert tracker.feedback_history == {}

    def test_record_push(self) -> None:
        tracker = PushTracker()
        tracker.record_push("ai")
        assert "ai" in tracker.last_push_at
        assert tracker.last_push_at["ai"] > 0

    def test_record_feedback_adopted(self) -> None:
        tracker = PushTracker()
        tracker.record_feedback("ai", FeedbackAction.ADOPTED)
        assert len(tracker.feedback_history["ai"]) == 1

    def test_three_consecutive_adoptions_shorten_interval(self) -> None:
        """连续 3 次采纳缩短间隔"""
        tracker = PushTracker()
        original = tracker.min_interval["ai"]
        tracker.record_feedback("ai", FeedbackAction.ADOPTED)
        tracker.record_feedback("ai", FeedbackAction.ADOPTED)
        tracker.record_feedback("ai", FeedbackAction.ADOPTED)
        assert tracker.min_interval["ai"] < original

    def test_dismiss_doubles_interval(self) -> None:
        """关闭弹窗延长间隔"""
        tracker = PushTracker()
        original = tracker.min_interval["ai"]
        tracker.record_feedback("ai", FeedbackAction.DISMISSED)
        assert tracker.min_interval["ai"] >= original * 2.0

    def test_dismiss_capped_at_120(self) -> None:
        """延长间隔上限 120 秒"""
        tracker = PushTracker()
        for _ in range(10):
            tracker.record_feedback("ai", FeedbackAction.DISMISSED)
        assert tracker.min_interval["ai"] <= 120.0

    def test_adoption_shorten_capped_at_1(self) -> None:
        """缩短间隔下限 1 秒"""
        tracker = PushTracker()
        for _ in range(20):
            tracker.record_feedback("ai", FeedbackAction.ADOPTED)
        assert tracker.min_interval["ai"] >= 1.0

    def test_serialize_deserialize(self) -> None:
        tracker = PushTracker()
        tracker.record_push("ai")
        tracker.record_feedback("ai", FeedbackAction.ADOPTED)

        data = tracker.to_dict()
        restored = PushTracker.from_dict(data)

        assert restored.min_interval == tracker.min_interval
        assert "ai" in restored.last_push_at

    def test_from_dict_none(self) -> None:
        tracker = PushTracker.from_dict(None)
        assert tracker.min_interval["ai"] == 3.0

    def test_feedback_history_capped_at_10(self) -> None:
        tracker = PushTracker()
        for _ in range(15):
            tracker.record_feedback("ai", FeedbackAction.ADOPTED)
        assert len(tracker.feedback_history["ai"]) <= 10

    def test_ignored_three_times_extends_interval(self) -> None:
        """连续忽略 3 次延长间隔"""
        tracker = PushTracker()
        original = tracker.min_interval["ai"]
        tracker.record_feedback("ai", FeedbackAction.IGNORED)
        tracker.record_feedback("ai", FeedbackAction.IGNORED)
        tracker.record_feedback("ai", FeedbackAction.IGNORED)
        assert tracker.min_interval["ai"] > original
