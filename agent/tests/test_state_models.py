"""编排层状态模型单元测试"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smartcs.shared.models import (
    ArbitrationResult,
    EmotionVector,
    ExecutorResult,
    FeedbackSignal,
    IntentLabel,
    OEState,
    OrchestrationState,
    RiskActionEnum,
    SentimentLabel,
    SessionState,
)


# ── RiskActionEnum ──


class TestRiskActionEnum:
    def test_values(self) -> None:
        assert RiskActionEnum.PASS == "PASS"
        assert RiskActionEnum.WARN == "WARN"
        assert RiskActionEnum.BLOCK == "BLOCK"

    def test_member_count(self) -> None:
        assert len(RiskActionEnum) == 3


# ── OEState ──


class TestOEState:
    def test_values(self) -> None:
        assert OEState.IDLE == "IDLE"
        assert OEState.EVALUATING == "EVALUATING"
        assert OEState.DISPATCHING == "DISPATCHING"
        assert OEState.WAITING_RESULTS == "WAITING_RESULTS"
        assert OEState.COMPLETED == "COMPLETED"

    def test_member_count(self) -> None:
        assert len(OEState) == 5


# ── EmotionVector ──


class TestEmotionVector:
    def test_creation_defaults(self) -> None:
        ev = EmotionVector(label=SentimentLabel.NEUTRAL, score=0.7)
        assert ev.label == SentimentLabel.NEUTRAL
        assert ev.score == 0.7
        assert ev.decay_lambda == 0.005
        assert isinstance(ev.updated_at, datetime)

    def test_decayed_score_zero_delta(self) -> None:
        ev = EmotionVector(label=SentimentLabel.NEGATIVE, score=0.8)
        assert ev.decayed_score(0.0) == pytest.approx(0.8)

    def test_decayed_score_positive_delta(self) -> None:
        ev = EmotionVector(label=SentimentLabel.ANGRY, score=1.0, decay_lambda=0.005)
        decayed = ev.decayed_score(100.0)
        # exp(-0.005 * 100) ≈ 0.6065
        assert decayed == pytest.approx(0.6065, rel=1e-3)
        assert decayed < 1.0

    def test_score_bounds(self) -> None:
        with pytest.raises(Exception):
            EmotionVector(label=SentimentLabel.POSITIVE, score=-0.1)
        with pytest.raises(Exception):
            EmotionVector(label=SentimentLabel.POSITIVE, score=1.5)

    def test_custom_decay_lambda(self) -> None:
        ev = EmotionVector(label=SentimentLabel.NEUTRAL, score=0.5, decay_lambda=0.01)
        decayed = ev.decayed_score(100.0)
        # exp(-0.01 * 100) ≈ 0.3679
        assert decayed == pytest.approx(0.5 * 0.3679, rel=1e-3)


# ── ExecutorResult ──


class TestExecutorResult:
    def test_defaults(self) -> None:
        er = ExecutorResult(executor_id="e1")
        assert er.executor_id == "e1"
        assert er.ui_schema == {}
        assert er.latency_ms == 0
        assert er.success is True
        assert er.degraded is False
        assert er.degradation_type == ""
        assert er.risk_action is None
        assert er.trace_id == ""

    def test_all_fields(self) -> None:
        er = ExecutorResult(
            executor_id="e3",
            ui_schema={"type": "alert"},
            latency_ms=50,
            success=True,
            degraded=False,
            degradation_type="",
            risk_action=RiskActionEnum.WARN,
            trace_id="abc-123",
        )
        assert er.risk_action == RiskActionEnum.WARN
        assert er.ui_schema["type"] == "alert"
        assert er.latency_ms == 50

    def test_degraded_result(self) -> None:
        er = ExecutorResult(
            executor_id="e1",
            success=True,
            degraded=True,
            degradation_type="llm_timeout",
        )
        assert er.degraded is True
        assert er.degradation_type == "llm_timeout"


# ── ArbitrationResult ──


class TestArbitrationResult:
    def test_defaults(self) -> None:
        ar = ArbitrationResult()
        assert ar.primary_card is None
        assert ar.risk_badge is None
        assert ar.marketing_slot is None
        assert ar.fusion_type == "service_only"
        assert ar.trace_id == ""

    def test_fusion_types(self) -> None:
        ar = ArbitrationResult(
            primary_card={"script": "hello"},
            risk_badge={"level": "warn"},
            marketing_slot={"product": "card_v2"},
            fusion_type="service_risk_marketing",
            trace_id="trace-001",
        )
        assert ar.fusion_type == "service_risk_marketing"
        assert ar.primary_card is not None
        assert ar.risk_badge is not None
        assert ar.marketing_slot is not None


# ── OrchestrationState ──


class TestOrchestrationState:
    def test_defaults(self) -> None:
        os = OrchestrationState(session_id="sess-1")
        assert os.session_id == "sess-1"
        assert os.oe_state == OEState.IDLE
        assert os.d1_activated is False
        assert os.d2_activated is False
        assert os.d3_activated is True  # 风控始终激活
        assert os.d1_cooldown_remaining == 0
        assert os.d2_cooldown_remaining == 0
        assert os.activation_history == []
        assert os.global_timeout_ms == 5000

    def test_d3_always_active(self) -> None:
        """风控评估器 D3 始终激活"""
        os = OrchestrationState(session_id="sess-2")
        assert os.d3_activated is True

    def test_state_transitions(self) -> None:
        os = OrchestrationState(session_id="sess-3")
        os.oe_state = OEState.EVALUATING
        assert os.oe_state == OEState.EVALUATING
        os.oe_state = OEState.DISPATCHING
        assert os.oe_state == OEState.DISPATCHING


# ── FeedbackSignal ──


class TestFeedbackSignal:
    def test_defaults(self) -> None:
        fs = FeedbackSignal(session_id="sess-1", agent_id="agent-1")
        assert fs.action == "reject"
        assert fs.confidence == 0.0
        assert fs.modify_fields == []

    def test_accept_action(self) -> None:
        fs = FeedbackSignal(session_id="sess-1", agent_id="agent-1", action="accept", confidence=1.0)
        assert fs.action == "accept"
        assert fs.confidence == 1.0

    def test_modify_action(self) -> None:
        fs = FeedbackSignal(
            session_id="sess-1",
            agent_id="agent-1",
            action="modify",
            confidence=0.5,
            modify_fields=["content", "tone"],
        )
        assert fs.action == "modify"
        assert fs.modify_fields == ["content", "tone"]

    def test_partial_accept_action(self) -> None:
        fs = FeedbackSignal(
            session_id="sess-1",
            agent_id="agent-1",
            action="partial_accept",
            confidence=0.3,
        )
        assert fs.action == "partial_accept"

    def test_invalid_action(self) -> None:
        with pytest.raises(Exception):
            FeedbackSignal(session_id="sess-1", agent_id="agent-1", action="invalid")  # type: ignore[arg-type]


# ── Extended SessionState ──


class TestExtendedSessionState:
    def test_new_fields_defaults(self) -> None:
        ss = SessionState(session_id="sess-1")
        assert ss.intent_stack == []
        assert ss.entity_pool == []
        assert ss.emotion_vector is None
        assert ss.suppress_flag is False
        assert ss.node_position == ""
        assert ss.risk_pending_audit is False

    def test_intent_stack(self) -> None:
        ss = SessionState(
            session_id="sess-1",
            intent_stack=[IntentLabel.FAQ, IntentLabel.BILL_QUERY],
        )
        assert len(ss.intent_stack) == 2
        assert ss.intent_stack[0] == IntentLabel.FAQ

    def test_emotion_vector(self) -> None:
        ev = EmotionVector(label=SentimentLabel.NEGATIVE, score=0.6)
        ss = SessionState(session_id="sess-1", emotion_vector=ev)
        assert ss.emotion_vector is not None
        assert ss.emotion_vector.label == SentimentLabel.NEGATIVE

    def test_suppress_flag_one_way_gate(self) -> None:
        """营销压制标记是单向门 false→true"""
        ss = SessionState(session_id="sess-1")
        assert ss.suppress_flag is False
        ss.suppress_flag = True
        assert ss.suppress_flag is True

    def test_risk_pending_audit(self) -> None:
        ss = SessionState(session_id="sess-1", risk_pending_audit=True)
        assert ss.risk_pending_audit is True

    def test_node_position(self) -> None:
        ss = SessionState(session_id="sess-1", node_position="classify")
        assert ss.node_position == "classify"
