"""Temporal 基础设施单元测试"""
from __future__ import annotations

from smartcs.workflows.shared import (
    EvaluatorInput,
    EvaluatorOutput,
    ExecutorInput,
    ExecutorOutput,
    OrchestrationResult,
)


class TestSharedModels:
    """共享数据模型"""

    def test_evaluator_input_defaults(self):
        inp = EvaluatorInput()
        assert inp.session_id == ""
        assert inp.state_snapshot == {}

    def test_evaluator_input_with_data(self):
        inp = EvaluatorInput(session_id="s1", state_snapshot={"version": 1})
        assert inp.session_id == "s1"

    def test_evaluator_output_defaults(self):
        out = EvaluatorOutput()
        assert out.activated is False
        assert out.cooldown_remaining == 0

    def test_executor_input_defaults(self):
        inp = ExecutorInput()
        assert inp.intent == "faq"
        assert inp.sentiment_history == []

    def test_executor_output_defaults(self):
        out = ExecutorOutput()
        assert out.executor_id == ""
        assert out.ui_schema == {}
        assert out.success is True

    def test_orchestration_result_defaults(self):
        result = OrchestrationResult()
        assert result.fusion_type == "service_only"
        assert result.primary_card == {}

    def test_orchestration_result_with_data(self):
        result = OrchestrationResult(
            primary_card={"type": "service"},
            fusion_type="service_marketing",
            trace_id="t1",
        )
        assert result.primary_card["type"] == "service"
        assert result.trace_id == "t1"


class TestTemporalSettings:
    """Temporal 配置"""

    def test_temporal_settings_defaults(self):
        from smartcs.shared.config import get_settings

        s = get_settings()
        assert s.temporal.host == "localhost"
        assert s.temporal.port == 7233
        assert s.temporal.namespace == "default"
        assert s.temporal.task_queue == "smartcs-assist"

    def test_temporal_settings_env_prefix(self):
        from smartcs.shared.config import TemporalSettings

        assert TemporalSettings.model_config["env_prefix"] == "TEMPORAL_"
