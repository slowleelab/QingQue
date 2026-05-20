"""Temporal Activities 单元测试

覆盖三路评估器 (D1/D2/D3) + 三路执行器 (E1/E2/E3)：
- D1: 高置信度激活、低置信度不激活、冷却期阻止
- D2: 正面情绪激活、suppress 阻止、冷却期阻止、负面情绪不激活
- D3: 始终激活
- E1: 正常返回结果、超时降级、熔断器打开降级、异常降级
- E2: 正常返回空卡片、熔断器打开降级
- E3: PASS/WARN/BLOCK 基于告警、熔断器打开降级、超时降级、异常降级
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.common.circuit_breaker import CircuitBreaker, CircuitState
from smartcs.workflows.activities import (
    evaluate_d1_service,
    evaluate_d2_marketing,
    evaluate_d3_risk,
    execute_e1_ai_service,
    execute_e2_marketing,
    execute_e3_risk,
    reset_breakers,
    reset_dedup_store,
    set_ai_dag,
    set_alert_engine_for_risk,
)
from smartcs.workflows.shared import EvaluatorInput, ExecutorInput


@pytest.fixture(autouse=True)
def _reset_module_state():
    """每个测试前后重置模块级单例"""
    reset_breakers()
    reset_dedup_store()
    yield
    reset_breakers()
    reset_dedup_store()


# ── D1 服务评估器 ──


class TestEvaluateD1Service:
    """D1 服务评估器: 意图置信度 > 阈值，2 轮冷却期"""

    @pytest.mark.asyncio
    async def test_high_confidence_activates(self) -> None:
        """高置信度 > 阈值 → 激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={"last_confidence": 0.9, "d1_cooldown_remaining": 0},
        )
        result = await evaluate_d1_service(inp)
        assert result.activated is True
        assert "0.90" in result.reason

    @pytest.mark.asyncio
    async def test_low_confidence_does_not_activate(self) -> None:
        """低置信度 <= 阈值 → 不激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={"last_confidence": 0.3, "d1_cooldown_remaining": 0},
        )
        result = await evaluate_d1_service(inp)
        assert result.activated is False

    @pytest.mark.asyncio
    async def test_exact_threshold_does_not_activate(self) -> None:
        """置信度 == 阈值 → 不激活（严格大于）"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={"last_confidence": 0.5, "d1_cooldown_remaining": 0},
        )
        result = await evaluate_d1_service(inp)
        assert result.activated is False

    @pytest.mark.asyncio
    async def test_cooldown_blocks_activation(self) -> None:
        """冷却期 > 0 → 不激活，即使置信度很高"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={"last_confidence": 0.99, "d1_cooldown_remaining": 2},
        )
        result = await evaluate_d1_service(inp)
        assert result.activated is False
        assert result.cooldown_remaining == 2
        assert "冷却" in result.reason

    @pytest.mark.asyncio
    async def test_missing_confidence_defaults_zero(self) -> None:
        """state_snapshot 缺少 last_confidence → 默认 0.0，不激活"""
        inp = EvaluatorInput(session_id="s1", state_snapshot={})
        result = await evaluate_d1_service(inp)
        assert result.activated is False

    @pytest.mark.asyncio
    async def test_zero_cooldown_allows_check(self) -> None:
        """冷却期为 0 允许评估，激活后返回冷却值"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={"last_confidence": 0.8, "d1_cooldown_remaining": 0},
        )
        result = await evaluate_d1_service(inp)
        assert result.activated is True
        # S3: 激活后返回 cooldown 值，供 Workflow CAS 写回
        assert result.cooldown_remaining == 2  # d1_cooldown_turns


# ── D2 营销评估器 ──


class TestEvaluateD2Marketing:
    """D2 营销评估器: 情绪 + 意图 + Suppress 综合判断，5 轮冷却期"""

    @pytest.mark.asyncio
    async def test_positive_emotion_activates(self) -> None:
        """正面情绪 + 高置信度 → 激活，返回冷却值"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.7,
                "emotion_vector": {"label": "positive", "score": 0.8},
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is True
        assert "positive" in result.reason
        # S3: 激活后返回 cooldown 值
        assert result.cooldown_remaining == 5  # d2_cooldown_turns

    @pytest.mark.asyncio
    async def test_neutral_emotion_activates(self) -> None:
        """中性情绪 + 高置信度 → 激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.8,
                "emotion_vector": {"label": "neutral", "score": 0.6},
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is True
        # S3: 激活后返回 cooldown 值
        assert result.cooldown_remaining == 5

    @pytest.mark.asyncio
    async def test_negative_emotion_does_not_activate(self) -> None:
        """负面情绪 → 不激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.9,
                "emotion_vector": {"label": "negative", "score": 0.9},
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is False
        assert "情绪不满足" in result.reason

    @pytest.mark.asyncio
    async def test_angry_emotion_does_not_activate(self) -> None:
        """愤怒情绪 → 不激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.9,
                "emotion_vector": {"label": "angry", "score": 0.9},
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is False

    @pytest.mark.asyncio
    async def test_suppress_flag_blocks(self) -> None:
        """suppress_flag=True → 不激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.9,
                "emotion_vector": {"label": "positive", "score": 0.9},
                "suppress_flag": True,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is False
        assert "压制" in result.reason

    @pytest.mark.asyncio
    async def test_cooldown_blocks(self) -> None:
        """冷却期 > 0 → 不激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.9,
                "emotion_vector": {"label": "positive", "score": 0.9},
                "suppress_flag": False,
                "d2_cooldown_remaining": 3,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is False
        assert result.cooldown_remaining == 3
        assert "冷却" in result.reason

    @pytest.mark.asyncio
    async def test_low_emotion_score_does_not_activate(self) -> None:
        """情绪 score 低于阈值 → 不激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.8,
                "emotion_vector": {"label": "positive", "score": 0.1},
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is False
        assert "条件不足" in result.reason

    @pytest.mark.asyncio
    async def test_low_confidence_does_not_activate(self) -> None:
        """置信度 <= 0.5 → 不激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.4,
                "emotion_vector": {"label": "positive", "score": 0.8},
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is False
        assert "条件不足" in result.reason

    @pytest.mark.asyncio
    async def test_missing_emotion_vector_does_not_activate(self) -> None:
        """缺少 emotion_vector → 不激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.8,
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is False

    @pytest.mark.asyncio
    async def test_none_emotion_vector_does_not_activate(self) -> None:
        """emotion_vector=None → 不激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.8,
                "emotion_vector": None,
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is False


# ── D3 风控评估器 ──


class TestEvaluateD3Risk:
    """D3 风控评估器: 始终激活"""

    @pytest.mark.asyncio
    async def test_always_activates(self) -> None:
        """无论输入什么，始终激活"""
        inp = EvaluatorInput(session_id="s1", state_snapshot={})
        result = await evaluate_d3_risk(inp)
        assert result.activated is True
        assert result.cooldown_remaining == 0

    @pytest.mark.asyncio
    async def test_activates_regardless_of_state(self) -> None:
        """即使有其他状态字段也始终激活"""
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={"last_confidence": 0.0, "suppress_flag": True},
        )
        result = await evaluate_d3_risk(inp)
        assert result.activated is True


# ── E1 AI 服务执行器 ──


class TestExecuteE1AIService:
    """E1 AI 服务执行器: LangGraph DAG"""

    @pytest.mark.asyncio
    async def test_normal_execution(self) -> None:
        """正常执行返回结果"""
        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(return_value={
            "ui_schema": {"scripts": [{"content": "test"}]},
            "degraded": False,
            "degradation_type": "",
        })
        set_ai_dag(mock_dag)

        inp = ExecutorInput(
            session_id="s1",
            message="你好",
            intent="faq",
            sentiment="neutral",
            state_snapshot={"last_confidence": 0.8},
            trace_id="trace-1",
        )
        result = await execute_e1_ai_service(inp)

        assert result.executor_id == "ai_service"
        assert result.success is True
        assert result.degraded is False
        assert result.ui_schema == {"scripts": [{"content": "test"}]}
        assert result.trace_id == "trace-1"
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_timeout_degrades(self) -> None:
        """超时降级为 safe_fallback（无快速通路结果时）"""
        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(side_effect=TimeoutError())
        set_ai_dag(mock_dag)

        inp = ExecutorInput(
            session_id="s1",
            message="你好",
            trace_id="trace-2",
        )
        result = await execute_e1_ai_service(inp)

        assert result.degraded is True
        # 修复: 无快速通路结果时降级到 safe_fallback
        assert result.degradation_type == "safe_fallback"
        assert result.trace_id == "trace-2"

    @pytest.mark.asyncio
    async def test_exception_degrades_to_safe_fallback(self) -> None:
        """异常降级为 safe_fallback"""
        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(side_effect=RuntimeError("LLM 不可用"))
        set_ai_dag(mock_dag)

        inp = ExecutorInput(
            session_id="s1",
            message="你好",
            trace_id="trace-3",
        )
        result = await execute_e1_ai_service(inp)

        assert result.degraded is True
        assert result.degradation_type == "safe_fallback"
        assert result.trace_id == "trace-3"

    @pytest.mark.asyncio
    async def test_breaker_open_degrades(self) -> None:
        """熔断器打开 → 直接降级"""
        # 手动将熔断器设为 OPEN
        from smartcs.workflows import activities as act

        breaker = CircuitBreaker(name="ai_executor", failure_threshold=0.5, sliding_window_size=4)
        for _ in range(4):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # 替换模块级熔断器
        act._ai_breaker = breaker

        inp = ExecutorInput(
            session_id="s1",
            message="你好",
            trace_id="trace-4",
        )
        result = await execute_e1_ai_service(inp)

        assert result.degraded is True
        assert result.degradation_type == "fast_path_fallback"
        assert result.trace_id == "trace-4"

    @pytest.mark.asyncio
    async def test_dag_receives_correct_kwargs(self) -> None:
        """DAG run 接收正确的关键字参数"""
        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(return_value={"ui_schema": {}, "degraded": False, "degradation_type": ""})
        set_ai_dag(mock_dag)

        inp = ExecutorInput(
            session_id="s1",
            message="查询账单",
            intent="bill_query",
            sentiment="neutral",
            state_snapshot={"last_confidence": 0.85},
            trace_id="trace-5",
        )
        await execute_e1_ai_service(inp)

        mock_dag.run.assert_called_once_with(
            session_id="s1",
            message="查询账单",
            intent="bill_query",
            sentiment="neutral",
            confidence=0.85,
            state_snapshot={"last_confidence": 0.85},
            trace_id="trace-5",
        )

    @pytest.mark.asyncio
    async def test_dag_degraded_result_propagated(self) -> None:
        """DAG 返回 degraded=True 时传播到 ExecutorOutput"""
        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(return_value={
            "ui_schema": {"fallback": "安全话术"},
            "degraded": True,
            "degradation_type": "safe_fallback",
        })
        set_ai_dag(mock_dag)

        inp = ExecutorInput(session_id="s1", message="你好", trace_id="trace-6")
        result = await execute_e1_ai_service(inp)

        assert result.degraded is True
        assert result.degradation_type == "safe_fallback"


# ── E2 营销执行器 ──


class TestExecuteE2Marketing:
    """E2 营销执行器: 纯 Activity 接口"""

    @pytest.mark.asyncio
    async def test_normal_returns_empty_cards(self) -> None:
        """正常返回空营销卡片"""
        inp = ExecutorInput(session_id="s1", message="你好", trace_id="trace-m1")
        result = await execute_e2_marketing(inp)

        assert result.executor_id == "marketing"
        assert result.ui_schema == {"marketing_cards": []}
        assert result.degraded is False
        assert result.trace_id == "trace-m1"

    @pytest.mark.asyncio
    async def test_breaker_open_degrades(self) -> None:
        """熔断器打开 → skip_card 降级"""
        from smartcs.workflows import activities as act

        breaker = CircuitBreaker(name="marketing_executor", failure_threshold=0.5, sliding_window_size=4)
        for _ in range(4):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        act._mkt_breaker = breaker

        inp = ExecutorInput(session_id="s1", message="你好", trace_id="trace-m2")
        result = await execute_e2_marketing(inp)

        assert result.degraded is True
        assert result.degradation_type == "skip_card"
        assert result.trace_id == "trace-m2"


# ── E3 风控执行器 ──


class TestExecuteE3Risk:
    """E3 风控执行器: 本地 AlertEngine"""

    @pytest.mark.asyncio
    async def test_pass_when_no_alerts(self) -> None:
        """无告警 → PASS"""
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(return_value=[])
        set_alert_engine_for_risk(mock_engine)

        inp = ExecutorInput(session_id="s1", message="你好", trace_id="trace-r1")
        result = await execute_e3_risk(inp)

        assert result.executor_id == "risk"
        assert result.risk_action == "PASS"
        assert result.ui_schema["action"] == "PASS"
        assert result.degraded is False
        assert result.trace_id == "trace-r1"
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_warn_on_warning_alerts(self) -> None:
        """Warning 告警 → WARN"""
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(return_value=[
            {"level": "warning", "message": "过度承诺用语", "category": "compliance", "suggestion": "请使用客观表述"},
        ])
        set_alert_engine_for_risk(mock_engine)

        inp = ExecutorInput(session_id="s1", message="保证100%批过", trace_id="trace-r2")
        result = await execute_e3_risk(inp)

        assert result.risk_action == "WARN"
        assert result.ui_schema["action"] == "WARN"
        assert len(result.ui_schema["alerts"]) == 1

    @pytest.mark.asyncio
    async def test_block_on_critical_alerts(self) -> None:
        """Critical 告警 → BLOCK"""
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(return_value=[
            {"level": "critical", "message": "疑似违规承诺", "category": "compliance", "suggestion": "立即停止"},
        ])
        set_alert_engine_for_risk(mock_engine)

        inp = ExecutorInput(session_id="s1", message="套现包过", trace_id="trace-r3")
        result = await execute_e3_risk(inp)

        assert result.risk_action == "BLOCK"
        assert result.ui_schema["action"] == "BLOCK"
        assert result.ui_schema["reason"] == "合规风险"
        assert len(result.ui_schema["alerts"]) == 1

    @pytest.mark.asyncio
    async def test_critical_takes_precedence_over_warning(self) -> None:
        """同时有 critical 和 warning → BLOCK（critical 优先）"""
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(return_value=[
            {"level": "warning", "message": "过度承诺", "category": "compliance", "suggestion": "客观表述"},
            {"level": "critical", "message": "违规承诺", "category": "compliance", "suggestion": "停止"},
        ])
        set_alert_engine_for_risk(mock_engine)

        inp = ExecutorInput(session_id="s1", message="保证套现包过", trace_id="trace-r4")
        result = await execute_e3_risk(inp)

        assert result.risk_action == "BLOCK"

    @pytest.mark.asyncio
    async def test_breaker_open_degrades(self) -> None:
        """熔断器打开 → pass_with_audit_flag 降级"""
        from smartcs.workflows import activities as act

        breaker = CircuitBreaker(name="risk_executor", failure_threshold=0.3, sliding_window_size=4)
        for _ in range(4):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        act._risk_breaker = breaker

        inp = ExecutorInput(session_id="s1", message="你好", trace_id="trace-r5")
        result = await execute_e3_risk(inp)

        assert result.degraded is True
        assert result.degradation_type == "pass_with_audit_flag"
        assert result.risk_action == "PASS"
        assert result.ui_schema["risk_pending_audit"] is True

    @pytest.mark.asyncio
    async def test_timeout_degrades(self) -> None:
        """超时 → pass_with_audit_flag 降级"""
        mock_engine = MagicMock()

        def slow_check(text: str) -> list:
            import time
            time.sleep(1.0)  # 超过 E3 SLA 100ms
            return []

        mock_engine.check_compliance = slow_check
        set_alert_engine_for_risk(mock_engine)

        inp = ExecutorInput(session_id="s1", message="你好", trace_id="trace-r6")
        result = await execute_e3_risk(inp)

        assert result.degraded is True
        assert result.degradation_type == "pass_with_audit_flag"
        assert result.risk_action == "PASS"
        assert result.ui_schema["risk_pending_audit"] is True

    @pytest.mark.asyncio
    async def test_exception_degrades(self) -> None:
        """异常 → pass_with_audit_flag 降级"""
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(side_effect=RuntimeError("规则引擎崩溃"))
        set_alert_engine_for_risk(mock_engine)

        inp = ExecutorInput(session_id="s1", message="你好", trace_id="trace-r7")
        result = await execute_e3_risk(inp)

        assert result.degraded is True
        assert result.degradation_type == "pass_with_audit_flag"
        assert result.risk_action == "PASS"
        assert result.ui_schema["risk_pending_audit"] is True

    @pytest.mark.asyncio
    async def test_check_compliance_receives_message(self) -> None:
        """AlertEngine.check_compliance 接收正确的 message 参数"""
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(return_value=[])
        set_alert_engine_for_risk(mock_engine)

        inp = ExecutorInput(session_id="s1", message="查询年费", trace_id="trace-r8")
        await execute_e3_risk(inp)

        mock_engine.check_compliance.assert_called_once_with("查询年费")


# ── 熔断器集成测试 ──


class TestBreakerIntegration:
    """验证 Activity 正确与熔断器交互"""

    @pytest.mark.asyncio
    async def test_e1_records_success_on_normal_call(self) -> None:
        """E1 正常调用记录成功到熔断器"""
        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(return_value={"ui_schema": {}, "degraded": False, "degradation_type": ""})
        set_ai_dag(mock_dag)

        inp = ExecutorInput(session_id="s1", message="你好", trace_id="t1")
        await execute_e1_ai_service(inp)

        # 熔断器应仍为 CLOSED
        from smartcs.workflows import activities as act
        breaker = act._get_ai_breaker()
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_e1_records_failure_on_exception(self) -> None:
        """E1 异常记录失败到熔断器"""
        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(side_effect=RuntimeError("boom"))
        set_ai_dag(mock_dag)

        # 多次异常应触发熔断（H5: 需要不同 trace_id 避免幂等命中）
        for i in range(10):
            inp = ExecutorInput(session_id="s1", message="你好", trace_id=f"t1-{i}")
            result = await execute_e1_ai_service(inp)
            assert result.degraded is True

        # 熔断器应最终打开
        from smartcs.workflows import activities as act
        breaker = act._get_ai_breaker()
        assert breaker.state == CircuitState.OPEN

        # 后续调用应直接走熔断降级
        result = await execute_e1_ai_service(inp)
        assert result.degraded is True
        assert result.degradation_type == "fast_path_fallback"

    @pytest.mark.asyncio
    async def test_e2_records_success_on_normal_call(self) -> None:
        """E2 正常调用记录成功到熔断器"""
        inp = ExecutorInput(session_id="s1", message="你好", trace_id="t1")
        await execute_e2_marketing(inp)

        from smartcs.workflows import activities as act
        breaker = act._get_mkt_breaker()
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_e3_records_failure_on_exception(self) -> None:
        """E3 异常记录失败到熔断器"""
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(side_effect=RuntimeError("规则引擎故障"))
        set_alert_engine_for_risk(mock_engine)

        # H5: 需要不同 trace_id 避免幂等命中
        for i in range(10):
            inp = ExecutorInput(session_id="s1", message="你好", trace_id=f"t1-{i}")
            result = await execute_e3_risk(inp)
            assert result.degraded is True

        from smartcs.workflows import activities as act
        breaker = act._get_risk_breaker()
        # 风控容忍度更低(30%)，应该更快熔断
        assert breaker.state == CircuitState.OPEN


# ── reset_breakers 工具函数测试 ──


class TestResetBreakers:
    """验证 reset_breakers 正确重置模块状态"""

    @pytest.mark.asyncio
    async def test_reset_clears_ai_breaker(self) -> None:
        """reset 后 AI 熔断器为 None"""
        from smartcs.workflows import activities as act

        act._get_ai_breaker()  # 初始化
        assert act._ai_breaker is not None
        reset_breakers()
        assert act._ai_breaker is None

    @pytest.mark.asyncio
    async def test_reset_clears_all_singletons(self) -> None:
        """reset 后所有单例为 None"""
        from smartcs.workflows import activities as act

        act._get_ai_breaker()
        act._get_mkt_breaker()
        act._get_risk_breaker()
        reset_breakers()
        assert act._ai_breaker is None
        assert act._mkt_breaker is None
        assert act._risk_breaker is None
        assert act._ai_dag is None
        assert act._risk_alert_engine is None
