"""编排层配置扩展单元测试"""

from __future__ import annotations

from smartcs.shared.config import (
    CircuitBreakerConfigSettings,
    OrchestrationSettings,
    Settings,
    TemporalSettings,
)

# ── OrchestrationSettings ──


class TestOrchestrationSettings:
    def test_defaults(self) -> None:
        s = OrchestrationSettings()
        # 评估器冷却轮数
        assert s.d1_cooldown_turns == 2
        assert s.d2_cooldown_turns == 5
        assert s.d3_always_active is True
        # 评估器激活阈值
        assert s.d1_intent_confidence_threshold == 0.5
        assert s.d2_emotion_score_threshold == 0.3
        # 全局超时
        assert s.global_timeout_ms == 5000
        # 执行器 SLA
        assert s.e1_sla_ms == 3000
        assert s.e2_sla_ms == 500
        assert s.e3_sla_ms == 100
        # 营销延迟
        assert s.marketing_defer_ms == 500

    def test_env_prefix(self) -> None:
        assert OrchestrationSettings.model_config.get("env_prefix") == "ORCH_"


# ── TemporalSettings ──


class TestTemporalSettings:
    def test_defaults(self) -> None:
        s = TemporalSettings()
        assert s.host == "localhost"
        assert s.port == 7233
        assert s.namespace == "default"
        assert s.task_queue == "smartcs-assist"
        assert s.workflow_timeout_seconds == 10

    def test_env_prefix(self) -> None:
        assert TemporalSettings.model_config.get("env_prefix") == "TEMPORAL_"


# ── CircuitBreakerConfigSettings ──


class TestCircuitBreakerConfigSettings:
    def test_ai_defaults(self) -> None:
        s = CircuitBreakerConfigSettings()
        assert s.ai_failure_rate_threshold == 0.5
        assert s.ai_slow_call_rate_threshold == 0.6
        assert s.ai_slow_call_duration_ms == 3000
        assert s.ai_wait_duration_open_s == 30.0
        assert s.ai_half_open_max_calls == 3
        assert s.ai_sliding_window_size == 20

    def test_mkt_defaults(self) -> None:
        s = CircuitBreakerConfigSettings()
        assert s.mkt_failure_rate_threshold == 0.5
        assert s.mkt_slow_call_rate_threshold == 0.5
        assert s.mkt_slow_call_duration_ms == 500
        assert s.mkt_wait_duration_open_s == 20.0
        assert s.mkt_sliding_window_size == 20

    def test_risk_defaults(self) -> None:
        s = CircuitBreakerConfigSettings()
        assert s.risk_failure_rate_threshold == 0.3
        assert s.risk_slow_call_rate_threshold == 0.3
        assert s.risk_slow_call_duration_ms == 100
        assert s.risk_wait_duration_open_s == 10.0
        assert s.risk_sliding_window_size == 20

    def test_env_prefix(self) -> None:
        assert CircuitBreakerConfigSettings.model_config.get("env_prefix") == "CB_"


# ── Settings 集成 ──


class TestSettingsIntegration:
    def test_has_orchestration_subconfig(self) -> None:
        s = Settings()
        assert hasattr(s, "orchestration")
        assert isinstance(s.orchestration, OrchestrationSettings)

    def test_has_temporal_subconfig(self) -> None:
        s = Settings()
        assert hasattr(s, "temporal")
        assert isinstance(s.temporal, TemporalSettings)

    def test_has_circuit_breaker_subconfig(self) -> None:
        s = Settings()
        assert hasattr(s, "circuit_breaker")
        assert isinstance(s.circuit_breaker, CircuitBreakerConfigSettings)

    def test_orchestration_defaults_propagated(self) -> None:
        s = Settings()
        assert s.orchestration.d1_cooldown_turns == 2
        assert s.orchestration.e1_sla_ms == 3000

    def test_temporal_defaults_propagated(self) -> None:
        s = Settings()
        assert s.temporal.host == "localhost"
        assert s.temporal.port == 7233

    def test_circuit_breaker_defaults_propagated(self) -> None:
        s = Settings()
        assert s.circuit_breaker.ai_failure_rate_threshold == 0.5
        assert s.circuit_breaker.risk_failure_rate_threshold == 0.3
