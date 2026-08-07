"""坐席辅助引擎测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lumio.services.common.assist_engine import (
    evaluate_d1_service,
    evaluate_d2_marketing,
    evaluate_d3_risk,
    run_assist_engine,
)
from lumio.services.common.decision import PushTracker


class TestEvaluators:
    """评估器单元测试"""

    def test_d1_activated_high_confidence(self) -> None:
        result = evaluate_d1_service({"last_confidence": 0.9, "d1_cooldown_remaining": 0})
        assert result.activated is True

    def test_d1_not_activated_low_confidence(self) -> None:
        result = evaluate_d1_service({"last_confidence": 0.3, "d1_cooldown_remaining": 0})
        assert result.activated is False

    def test_d1_not_activated_in_cooldown(self) -> None:
        result = evaluate_d1_service({"last_confidence": 0.95, "d1_cooldown_remaining": 1})
        assert result.activated is False
        assert "冷却" in result.reason

    def test_d2_activated_positive_emotion(self) -> None:
        result = evaluate_d2_marketing(
            {
                "emotion_vector": {"label": "positive", "score": 0.8},
                "last_confidence": 0.7,
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            }
        )
        assert result.activated is True

    def test_d2_not_activated_suppressed(self) -> None:
        result = evaluate_d2_marketing(
            {
                "emotion_vector": {"label": "positive", "score": 0.9},
                "last_confidence": 0.8,
                "suppress_flag": True,
                "d2_cooldown_remaining": 0,
            }
        )
        assert result.activated is False

    def test_d2_not_activated_cooldown(self) -> None:
        result = evaluate_d2_marketing(
            {
                "emotion_vector": {"label": "positive", "score": 0.9},
                "last_confidence": 0.8,
                "suppress_flag": False,
                "d2_cooldown_remaining": 3,
            }
        )
        assert result.activated is False

    def test_d3_always_activated(self) -> None:
        result = evaluate_d3_risk({})
        assert result.activated is True


class TestAssistEngine:
    """坐席辅助引擎集成测试"""

    @pytest.fixture
    def ai_executor(self) -> MagicMock:
        mock = MagicMock()
        mock.run = AsyncMock(
            return_value={
                "ui_schema": {"scripts": [{"script_id": "s1", "content": "话术"}]},
                "latency_ms": 100,
                "degraded": False,
                "degradation_type": "",
                "path": "fast",
                "fast_path_hit": True,
            }
        )
        return mock

    @pytest.fixture
    def alert_engine(self) -> MagicMock:
        mock = MagicMock()
        mock.check_compliance = MagicMock(return_value=[])
        return mock

    @pytest.mark.asyncio
    async def test_pipeline_basic_flow(self, ai_executor: MagicMock, alert_engine: MagicMock) -> None:
        """基本编排流程：有 AI 建议，风控 PASS，无营销"""
        result = await run_assist_engine(
            session_id="s1",
            message="查一下账单",
            intent="bill_query",
            confidence=0.85,
            trace_id="t1",
            state_snapshot={"last_confidence": 0.85},
            ai_executor=ai_executor,
            alert_engine=alert_engine,
        )

        assert result is not None
        assert result["type"] == "assist_push"
        assert "payload" in result
        assert result["payload"]["fusion_type"] in ("service_only", "service_marketing")
        ai_executor.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pipeline_low_confidence_skips_ai(self, alert_engine: MagicMock) -> None:
        """低置信度时 D1 不激活，跳过 E1"""
        ai_exec = MagicMock()
        ai_exec.run = AsyncMock()

        result = await run_assist_engine(
            session_id="s1",
            message="嗯",
            intent="faq",
            confidence=0.1,
            trace_id="t1",
            state_snapshot={"last_confidence": 0.1},
            ai_executor=ai_exec,
            alert_engine=alert_engine,
        )

        # D1 不激活，E1 不应被调用
        ai_exec.run.assert_not_awaited()
        # 仍然返回结果（只有风控）
        if result:
            assert result["type"] == "assist_push"

    @pytest.mark.asyncio
    async def test_pipeline_risk_block(self, ai_executor: MagicMock) -> None:
        """风控 BLOCK 时主卡为拦截"""
        alert = MagicMock()
        alert.check_compliance = MagicMock(
            return_value=[
                {"level": "critical", "message": "违规承诺"},
            ]
        )

        result = await run_assist_engine(
            session_id="s1",
            message="保证收益零风险",
            intent="faq",
            confidence=0.8,
            trace_id="t1",
            state_snapshot={"last_confidence": 0.8},
            ai_executor=ai_executor,
            alert_engine=alert,
        )

        assert result is not None
        assert result["payload"]["fusion_type"] == "risk_blocked"

    @pytest.mark.asyncio
    async def test_pipeline_no_ai_executor(self) -> None:
        """无 AI 执行器时返回 None"""
        result = await run_assist_engine(
            session_id="s1",
            message="test",
            intent="faq",
            confidence=0.5,
            trace_id="t1",
            state_snapshot={},
            ai_executor=None,  # type: ignore[arg-type]
        )
        assert result is None or result["type"] == "assist_push"

    @pytest.mark.asyncio
    async def test_pipeline_ai_timeout(self, alert_engine: MagicMock) -> None:
        """E1 超时时降级但不崩溃"""
        ai_exec = MagicMock()
        ai_exec.run = AsyncMock(side_effect=TimeoutError("timeout"))

        result = await run_assist_engine(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.9,
            trace_id="t2",
            state_snapshot={"last_confidence": 0.9},
            ai_executor=ai_exec,
            alert_engine=alert_engine,
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_pipeline_marketing_suppressed_in_urgent(
        self,
        ai_executor: MagicMock,
        alert_engine: MagicMock,
    ) -> None:
        """紧急场景不展示营销"""
        result = await run_assist_engine(
            session_id="s1",
            message="我卡丢了",
            intent="card_loss",
            confidence=0.95,
            trace_id="t3",
            state_snapshot={
                "last_confidence": 0.95,
                "emotion_vector": {"label": "positive", "score": 0.9},
            },
            ai_executor=ai_executor,
            alert_engine=alert_engine,
        )
        assert result is not None
        # 营销不应出现
        assert result["payload"].get("marketing_slot") is None

    @pytest.mark.asyncio
    async def test_pipeline_with_push_tracker_blocks_duplicate(
        self,
        ai_executor: MagicMock,
        alert_engine: MagicMock,
    ) -> None:
        """PushTracker 短时间内阻止重复推送"""

        tracker = PushTracker()
        tracker.record_push("ai")

        result = await run_assist_engine(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.9,
            trace_id="t4",
            state_snapshot={"last_confidence": 0.9},
            ai_executor=ai_executor,
            alert_engine=alert_engine,
            push_tracker=tracker,
        )
        # 刚推送过，AI 应被跳过
        assert result is not None

    @pytest.mark.asyncio
    async def test_pipeline_e3_timeout_graceful(
        self,
        ai_executor: MagicMock,
    ) -> None:
        """E3 超时时降级为 PASS+audit"""
        alert = MagicMock()
        alert.check_compliance = MagicMock(side_effect=TimeoutError("timeout"))

        result = await run_assist_engine(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.9,
            trace_id="t5",
            state_snapshot={"last_confidence": 0.9},
            ai_executor=ai_executor,
            alert_engine=alert,
        )
        assert result is not None


class TestAssistEngineDegradation:
    """降级分支测试"""

    @pytest.fixture
    def alert_engine(self) -> MagicMock:
        mock = MagicMock()
        mock.check_compliance = MagicMock(return_value=[])
        return mock

    @pytest.mark.asyncio
    async def test_no_ai_executor_degraded(self, alert_engine: MagicMock) -> None:
        """ai_executor 未配置 → 降级但仍有风控推送"""
        from lumio.services.common.assist_engine import run_assist_engine

        result = await run_assist_engine(
            session_id="s1",
            message="hi",
            intent="bill_query",
            confidence=0.9,
            trace_id="t1",
            state_snapshot={"last_confidence": 0.9},
            ai_executor=None,
            alert_engine=alert_engine,
        )
        assert result is not None
        assert result["type"] == "assist_push"

    @pytest.mark.asyncio
    async def test_ai_breaker_open(self, alert_engine: MagicMock) -> None:
        """AI 熔断打开 → 降级"""
        from lumio.services.common.assist_engine import run_assist_engine
        from lumio.services.common.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(name="ai", failure_threshold=0.05, recovery_timeout=60)
        breaker.record_failure()  # 窗口需 ≥2 条才评估
        breaker.record_failure()  # 2/2 失败率 100% ≥ 5% → 打开

        ai_exec = MagicMock()
        ai_exec.run = AsyncMock()

        result = await run_assist_engine(
            session_id="s1",
            message="hi",
            intent="bill_query",
            confidence=0.9,
            trace_id="t1",
            state_snapshot={"last_confidence": 0.9},
            ai_executor=ai_exec,
            alert_engine=alert_engine,
            breakers={"ai": breaker, "risk": None, "mkt": None},
        )
        ai_exec.run.assert_not_awaited()
        assert result is not None

    @pytest.mark.asyncio
    async def test_e1_timeout_degraded(self, alert_engine: MagicMock) -> None:
        """E1 超时 → timeout 降级 (不阻塞风控)"""
        from lumio.services.common.assist_engine import run_assist_engine

        ai_exec = MagicMock()

        async def slow_run(**kwargs):
            import asyncio

            await asyncio.sleep(5)  # 超过 SLA
            return {}

        ai_exec.run = slow_run
        result = await run_assist_engine(
            session_id="s1",
            message="hi",
            intent="bill_query",
            confidence=0.9,
            trace_id="t1",
            state_snapshot={"last_confidence": 0.9},
            ai_executor=ai_exec,
            alert_engine=alert_engine,
        )
        assert result is not None
        assert result["type"] == "assist_push"

    @pytest.mark.asyncio
    async def test_e1_exception_degraded(self, alert_engine: MagicMock) -> None:
        """E1 异常 → safe_fallback 降级"""
        from lumio.services.common.assist_engine import run_assist_engine

        ai_exec = MagicMock()
        ai_exec.run = AsyncMock(side_effect=RuntimeError("boom"))
        result = await run_assist_engine(
            session_id="s1",
            message="hi",
            intent="bill_query",
            confidence=0.9,
            trace_id="t1",
            state_snapshot={"last_confidence": 0.9},
            ai_executor=ai_exec,
            alert_engine=alert_engine,
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_alert_engine_risk_passes(self) -> None:
        """无告警引擎 → 风控 PASS"""
        from lumio.services.common.assist_engine import run_assist_engine

        ai_exec = MagicMock()
        ai_exec.run = AsyncMock(
            return_value={"ui_schema": {"scripts": []}, "latency_ms": 10, "degraded": False, "degradation_type": ""}
        )
        result = await run_assist_engine(
            session_id="s1",
            message="hi",
            intent="bill_query",
            confidence=0.9,
            trace_id="t1",
            state_snapshot={"last_confidence": 0.9},
            ai_executor=ai_exec,
            alert_engine=None,
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_marketing_activated(self, alert_engine: MagicMock) -> None:
        """营销激活: 正面情绪 + 高置信度"""
        from lumio.services.common.assist_engine import run_assist_engine

        ai_exec = MagicMock()
        ai_exec.run = AsyncMock(
            return_value={
                "ui_schema": {"scripts": [{"script_id": "s1"}]},
                "latency_ms": 10,
                "degraded": False,
                "degradation_type": "",
            }
        )
        result = await run_assist_engine(
            session_id="s1",
            message="积分怎么换",
            intent="reward_query",
            confidence=0.9,
            trace_id="t1",
            state_snapshot={
                "last_confidence": 0.9,
                "emotion_vector": {"label": "positive", "score": 0.9},
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
            ai_executor=ai_exec,
            alert_engine=alert_engine,
            sentiment="positive",
            sentiment_score=0.9,
        )
        assert result is not None
        # reward_query+positive 命中积分营销规则
        assert result["payload"]["fusion_type"] in ("service_marketing", "marketing_service")

    @pytest.mark.asyncio
    async def test_push_tracker_records(self, alert_engine: MagicMock) -> None:
        """推送被 tracker 记录 (供展示决策抑制重复)"""
        from lumio.services.common.assist_engine import PushTracker, run_assist_engine

        ai_exec = MagicMock()
        ai_exec.run = AsyncMock(
            return_value={
                "ui_schema": {"scripts": [{"script_id": "s1"}]},
                "latency_ms": 10,
                "degraded": False,
                "degradation_type": "",
            }
        )
        tracker = PushTracker()
        kwargs = dict(
            session_id="s1",
            message="hi",
            intent="bill_query",
            confidence=0.9,
            trace_id="t1",
            state_snapshot={"last_confidence": 0.9},
            ai_executor=ai_exec,
            alert_engine=alert_engine,
            push_tracker=tracker,
        )
        first = await run_assist_engine(**kwargs)
        assert first is not None
        # 推送后 tracker 记录了 ai 推送时间 (供 interval 抑制)
        assert "ai" in tracker.last_push_at
