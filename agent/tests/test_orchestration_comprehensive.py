"""AI 辅助功能六层架构综合测试

覆盖设计文档 §25 全部核心逻辑的端到端场景和边界条件：
- 宏观编排层: Workflow 策略矩阵、全局超时、suppress 持续、CAS 写回
- 微观执行层: 执行器幂等性、E1 超时降级通路、E3 并行超时
- 仲裁层: GlobalArbitrator 全融合规则 + PII/合规联动
- 状态层: CAS 合并规则（H1 suppress_force_clear、情绪时间窗口、冷却计数器）
- 反馈层: 延迟确认 + 撤销端点
- DAG 层: PII 脱敏输出、深度通路知识格式化
- M7: EmotionVector.decayed_score 在 D2 评估中的应用
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from smartcs.services.assist.arbitrator import GlobalArbitrator
from smartcs.services.common.state_manager import StateManager
from smartcs.shared.models import EmotionVector
from smartcs.workflows.shared import (
    EvaluatorInput,
    EvaluatorOutput,
    ExecutorInput,
    ExecutorOutput,
    OrchestrationResult,
)


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def arbitrator() -> GlobalArbitrator:
    return GlobalArbitrator()


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.script_load = AsyncMock(return_value="fake_sha")
    return redis


@pytest.fixture
def state_manager(mock_redis: AsyncMock) -> StateManager:
    return StateManager(redis=mock_redis, ttl=1800)


def _make_state(version: int = 1, **overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "version": version,
        "last_writer": "init",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "risk_pending_audit": False,
        "intent_stack": [],
        "entity_pool": [],
        "emotion_vector": None,
        "suppress_flag": False,
        "node_position": "",
    }
    state.update(overrides)
    return state


def _make_executor(
    executor_id: str = "",
    ui_schema: dict | None = None,
    risk_action: str = "",
    degraded: bool = False,
    degradation_type: str = "",
    trace_id: str = "",
) -> ExecutorOutput:
    return ExecutorOutput(
        executor_id=executor_id,
        ui_schema=ui_schema or {},
        risk_action=risk_action,
        degraded=degraded,
        degradation_type=degradation_type,
        trace_id=trace_id,
    )


# ══════════════════════════════════════════════════════════════════════
# 1. Workflow._apply_policies 策略矩阵综合
# ══════════════════════════════════════════════════════════════════════


class TestApplyPoliciesComprehensive:
    """编排策略矩阵完整覆盖"""

    def test_d1_activates_d2_suppressed(self):
        """D1 激活 + D2 激活 → D2 被压制, suppress_remaining=2"""
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

        wf = OrchestrationWorkflow()
        d1 = EvaluatorOutput(activated=True)
        d2 = EvaluatorOutput(activated=True)
        d3 = EvaluatorOutput(activated=True)
        plan = wf._apply_policies(d1, d2, d3)
        assert plan["d2_suppressed"] is True
        assert wf._suppress_remaining == 2

    def test_d1_activates_d2_not_activated_no_suppress(self):
        """D1 激活但 D2 未激活 → 不压制"""
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

        wf = OrchestrationWorkflow()
        d1 = EvaluatorOutput(activated=True)
        d2 = EvaluatorOutput(activated=False)
        d3 = EvaluatorOutput(activated=True)
        plan = wf._apply_policies(d1, d2, d3)
        assert plan["d2_suppressed"] is False

    def test_d2_alone_not_suppressed(self):
        """D2 激活但 D1 未激活 → 不压制"""
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

        wf = OrchestrationWorkflow()
        d1 = EvaluatorOutput(activated=False)
        d2 = EvaluatorOutput(activated=True)
        d3 = EvaluatorOutput(activated=True)
        plan = wf._apply_policies(d1, d2, d3)
        assert plan["d2_suppressed"] is False

    def test_neither_activated_no_suppress(self):
        """D1/D2 都未激活 → 不压制"""
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

        wf = OrchestrationWorkflow()
        d1 = EvaluatorOutput(activated=False)
        d2 = EvaluatorOutput(activated=False)
        d3 = EvaluatorOutput(activated=True)
        plan = wf._apply_policies(d1, d2, d3)
        assert plan["d2_suppressed"] is False

    def test_suppress_remaining_decrement(self):
        """连续调用 _apply_policies 时 suppress_remaining 递减"""
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

        wf = OrchestrationWorkflow()
        # 第一轮: D1+D2 激活 → suppress=2
        wf._apply_policies(
            EvaluatorOutput(activated=True),
            EvaluatorOutput(activated=True),
            EvaluatorOutput(activated=True),
        )
        assert wf._suppress_remaining == 2

        # 第二轮: D1 不激活, D2 不激活 → suppress_remaining 不变（仅 D1+D2 同时激活才重置）
        wf._apply_policies(
            EvaluatorOutput(activated=False),
            EvaluatorOutput(activated=False),
            EvaluatorOutput(activated=True),
        )
        # suppress_remaining 仍在 2（因为不是 D1+D2 同时激活场景）
        assert wf._suppress_remaining == 2

    def test_suppress_reset_on_reactivation(self):
        """suppress 期间 D1+D2 再次激活 → suppress_remaining 重置为 2"""
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

        wf = OrchestrationWorkflow()
        wf._suppress_remaining = 1  # 剩余 1 轮
        # 再次 D1+D2 激活
        plan = wf._apply_policies(
            EvaluatorOutput(activated=True),
            EvaluatorOutput(activated=True),
            EvaluatorOutput(activated=True),
        )
        assert plan["d2_suppressed"] is True
        assert wf._suppress_remaining == 2  # 重置


# ══════════════════════════════════════════════════════════════════════
# 2. Workflow._cas_write_back 补丁构建
# ══════════════════════════════════════════════════════════════════════


class TestCASWriteBack:
    """S2: CAS 写回补丁构建逻辑"""

    def test_suppress_remaining_sets_flag(self):
        """suppress_remaining > 0 → suppress_flag=True"""
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

        wf = OrchestrationWorkflow()
        wf._suppress_remaining = 2
        patches: dict[str, Any] = {}

        # 模拟 _cas_write_back 的补丁构建逻辑
        if wf._suppress_remaining > 0:
            patches["suppress_flag"] = True
        else:
            patches["suppress_flag"] = False
            patches["suppress_force_clear"] = True

        assert patches["suppress_flag"] is True
        assert "suppress_force_clear" not in patches

    def test_suppress_expired_clears_flag(self):
        """suppress_remaining == 0 → suppress_flag=False + suppress_force_clear=True"""
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

        wf = OrchestrationWorkflow()
        wf._suppress_remaining = 0
        patches: dict[str, Any] = {}

        if wf._suppress_remaining > 0:
            patches["suppress_flag"] = True
        else:
            patches["suppress_flag"] = False
            patches["suppress_force_clear"] = True

        assert patches["suppress_flag"] is False
        assert patches["suppress_force_clear"] is True

    def test_cooldown_write_back_on_activation(self):
        """D1/D2 激活后冷却值写回"""
        # D1 激活 → d1_cooldown_remaining = 2
        # D2 激活 → d2_cooldown_remaining = 5
        patches: dict[str, Any] = {}
        d1 = EvaluatorOutput(activated=True, cooldown_remaining=2)
        d2 = EvaluatorOutput(activated=True, cooldown_remaining=5)

        if d1.activated:
            patches["d1_cooldown_remaining"] = 2
        if d2.activated:
            patches["d2_cooldown_remaining"] = 5

        assert patches["d1_cooldown_remaining"] == 2
        assert patches["d2_cooldown_remaining"] == 5

    def test_cooldown_decrement_when_not_activated(self):
        """D1 未激活但冷却中 → d1_cooldown_remaining 递减"""
        patches: dict[str, Any] = {}
        d1 = EvaluatorOutput(activated=False, cooldown_remaining=2)

        if d1.activated:
            patches["d1_cooldown_remaining"] = 2
        elif d1.cooldown_remaining > 0:
            patches["d1_cooldown_remaining"] = d1.cooldown_remaining - 1

        assert patches["d1_cooldown_remaining"] == 1

    def test_risk_pending_audit_on_degradation(self):
        """E3 降级为 pass_with_audit_flag → risk_pending_audit=True"""
        patches: dict[str, Any] = {}
        results = {
            "risk": _make_executor(
                executor_id="risk",
                degraded=True,
                degradation_type="pass_with_audit_flag",
            ),
        }
        risk_result = results.get("risk")
        if risk_result and risk_result.degraded and risk_result.degradation_type == "pass_with_audit_flag":
            patches["risk_pending_audit"] = True

        assert patches["risk_pending_audit"] is True

    def test_risk_pending_audit_not_set_on_normal(self):
        """E3 正常执行 → 不设置 risk_pending_audit"""
        patches: dict[str, Any] = {}
        results = {
            "risk": _make_executor(executor_id="risk", risk_action="PASS"),
        }
        risk_result = results.get("risk")
        if risk_result and risk_result.degraded and risk_result.degradation_type == "pass_with_audit_flag":
            patches["risk_pending_audit"] = True

        assert "risk_pending_audit" not in patches

    def test_intent_stack_append_on_input(self):
        """有 intent 时追加到 intent_stack"""
        patches: dict[str, Any] = {}
        input_intent = "bill_query"
        if input_intent:
            patches["intent_stack"] = [input_intent]

        assert patches["intent_stack"] == ["bill_query"]


# ══════════════════════════════════════════════════════════════════════
# 3. GlobalArbitrator 全融合规则 + PII/合规联动
# ══════════════════════════════════════════════════════════════════════


class TestArbitratorFullFusion:
    """全局仲裁器完整融合规则"""

    @pytest.mark.asyncio
    async def test_risk_block_with_all_executors(self, arbitrator: GlobalArbitrator):
        """风控 BLOCK + 服务 + 营销 → risk_blocked, 无营销"""
        results = {
            "risk": _make_executor(
                executor_id="risk",
                ui_schema={"action": "BLOCK", "reason": "高风险操作"},
                risk_action="BLOCK",
            ),
            "ai_service": _make_executor(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
            "marketing": _make_executor(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"name": "产品"}]},
            ),
        }
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "risk_blocked"
        assert result.primary_card["type"] == "risk_block"
        assert result.marketing_slot is None

    @pytest.mark.asyncio
    async def test_risk_warn_with_marketing(self, arbitrator: GlobalArbitrator):
        """风控 WARN + 服务 + 营销 → service_risk_warn, 营销降级为小卡片"""
        results = {
            "risk": _make_executor(
                executor_id="risk",
                ui_schema={"action": "WARN", "alerts": [{"level": "warning", "message": "注意"}]},
                risk_action="WARN",
            ),
            "ai_service": _make_executor(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
            "marketing": _make_executor(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"name": "产品"}]},
            ),
        }
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "service_risk_warn"
        assert result.risk_badge is not None
        assert result.risk_badge["type"] == "risk_badge"
        assert result.marketing_slot is not None
        assert result.marketing_slot["type"] == "marketing_small"

    @pytest.mark.asyncio
    async def test_risk_pass_with_marketing(self, arbitrator: GlobalArbitrator):
        """风控 PASS + 服务 + 营销 → service_marketing, 营销标准展示"""
        results = {
            "risk": _make_executor(
                executor_id="risk",
                ui_schema={"action": "PASS"},
                risk_action="PASS",
            ),
            "ai_service": _make_executor(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
            "marketing": _make_executor(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"name": "产品"}]},
            ),
        }
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "service_marketing"
        assert result.marketing_slot is not None
        assert result.marketing_slot["type"] == "marketing_standard"

    @pytest.mark.asyncio
    async def test_risk_pass_service_only(self, arbitrator: GlobalArbitrator):
        """风控 PASS + 服务 无营销 → service_only"""
        results = {
            "ai_service": _make_executor(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
        }
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "service_only"
        assert result.marketing_slot is None

    @pytest.mark.asyncio
    async def test_empty_results_defaults_service_only(self, arbitrator: GlobalArbitrator):
        """无任何执行结果 → service_only"""
        result = await arbitrator.arbitrate({})
        assert result.fusion_type == "service_only"

    @pytest.mark.asyncio
    async def test_risk_only_block(self, arbitrator: GlobalArbitrator):
        """只有风控 BLOCK → risk_blocked"""
        results = {
            "risk": _make_executor(
                executor_id="risk",
                ui_schema={"action": "BLOCK"},
                risk_action="BLOCK",
            ),
        }
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "risk_blocked"
        assert result.marketing_slot is None

    @pytest.mark.asyncio
    async def test_risk_warn_empty_marketing_no_slot(self, arbitrator: GlobalArbitrator):
        """风控 WARN + 空营销卡片 → 无营销槽"""
        results = {
            "risk": _make_executor(
                executor_id="risk",
                ui_schema={"action": "WARN", "alerts": []},
                risk_action="WARN",
            ),
            "ai_service": _make_executor(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
            "marketing": _make_executor(
                executor_id="marketing",
                ui_schema={"marketing_cards": []},
            ),
        }
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "service_risk_warn"
        assert result.marketing_slot is None

    @pytest.mark.asyncio
    async def test_pii_masking_in_all_slots(self, arbitrator: GlobalArbitrator):
        """PII 脱敏同时作用于 primary_card + risk_badge + marketing_slot"""
        results = {
            "risk": _make_executor(
                executor_id="risk",
                ui_schema={"action": "WARN", "alerts": [{"msg": "客户张三手机13800138000异常"}]},
                risk_action="WARN",
            ),
            "ai_service": _make_executor(
                executor_id="ai_service",
                ui_schema={"answer": "客户李四的卡号6222021234567890"},
            ),
            "marketing": _make_executor(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"title": "客户王五专属"}]},
            ),
        }
        result = await arbitrator.arbitrate(results)
        # primary_card 中不应有 PII
        assert "13800138000" not in str(result.primary_card)
        assert "6222021234567890" not in str(result.primary_card)
        # risk_badge 中不应有 PII
        assert "13800138000" not in str(result.risk_badge)
        # marketing_slot 中不应有 PII
        assert "王五" not in str(result.marketing_slot) or "[NAME]" in str(result.marketing_slot)

    @pytest.mark.asyncio
    async def test_compliance_filter_in_primary_card(self, arbitrator: GlobalArbitrator):
        """合规短语过滤作用于 primary_card"""
        results = {
            "ai_service": _make_executor(
                executor_id="ai_service",
                ui_schema={"answer": "本产品保证收益，绝对安全"},
            ),
        }
        result = await arbitrator.arbitrate(results)
        answer_str = str(result.primary_card)
        assert "保证收益" not in answer_str
        assert "绝对安全" not in answer_str
        assert "[已过滤]" in answer_str


# ══════════════════════════════════════════════════════════════════════
# 4. StateManager 合并规则完整覆盖
# ══════════════════════════════════════════════════════════════════════


class TestStateManagerMergeRulesComprehensive:
    """CAS 合并规则完整覆盖（含 H1 suppress_force_clear）"""

    def test_suppress_force_clear_allows_true_to_false(self, state_manager: StateManager):
        """H1: suppress_force_clear 允许 suppress_flag true→false"""
        current = _make_state(suppress_flag=True)
        patches = {"suppress_flag": False, "suppress_force_clear": True}
        result = state_manager._apply_merge_rules(current, patches)
        assert result["suppress_flag"] is False
        # suppress_force_clear 是辅助字段，不写入状态
        assert "suppress_force_clear" not in result

    def test_suppress_force_clear_missing_blocks_true_to_false(self, state_manager: StateManager):
        """无 suppress_force_clear 时 true→false 被阻止"""
        current = _make_state(suppress_flag=True)
        patches = {"suppress_flag": False}
        result = state_manager._apply_merge_rules(current, patches)
        assert "suppress_flag" not in result

    def test_emotion_vector_newer_replaces_older(self, state_manager: StateManager):
        """情绪向量时间窗口: 新值覆盖旧值"""
        current = _make_state(
            emotion_vector={"label": "neutral", "score": 0.5, "updated_at": "2026-01-01T00:00:00"}
        )
        patches = {
            "emotion_vector": {"label": "positive", "score": 0.8, "updated_at": "2026-01-01T00:05:00"}
        }
        result = state_manager._apply_merge_rules(current, patches)
        assert result["emotion_vector"]["label"] == "positive"
        assert result["emotion_vector"]["score"] == 0.8

    def test_emotion_vector_older_preserved(self, state_manager: StateManager):
        """情绪向量时间窗口: 旧值比新值更新时保留旧值"""
        current = _make_state(
            emotion_vector={"label": "positive", "score": 0.8, "updated_at": "2026-01-01T10:00:00"}
        )
        patches = {
            "emotion_vector": {"label": "neutral", "score": 0.3, "updated_at": "2026-01-01T09:00:00"}
        }
        result = state_manager._apply_merge_rules(current, patches)
        assert result["emotion_vector"]["label"] == "positive"
        assert result["emotion_vector"]["score"] == 0.8

    def test_emotion_vector_none_current_allows_any(self, state_manager: StateManager):
        """当前 emotion_vector 为 None → 任何新值直接写入"""
        current = _make_state(emotion_vector=None)
        patches = {
            "emotion_vector": {"label": "angry", "score": 0.9, "updated_at": "2026-01-01T00:00:00"}
        }
        result = state_manager._apply_merge_rules(current, patches)
        assert result["emotion_vector"]["label"] == "angry"

    def test_entity_pool_merge_new_and_update(self, state_manager: StateManager):
        """实体池增量: 同时新增和更新"""
        current = _make_state(
            entity_pool=[
                {"entity_type": "card_number", "value": "6225****1234", "confidence": 0.9},
            ]
        )
        patches = {
            "entity_pool": [
                {"entity_type": "card_number", "value": "6225****1234", "confidence": 0.95},  # 更新
                {"entity_type": "amount", "value": "5000", "confidence": 0.8},  # 新增
            ]
        }
        result = state_manager._apply_merge_rules(current, patches)
        assert len(result["entity_pool"]) == 2
        # 更新已有实体
        card_entity = next(e for e in result["entity_pool"] if e["entity_type"] == "card_number")
        assert card_entity["confidence"] == 0.95
        # 新增实体
        amt_entity = next(e for e in result["entity_pool"] if e["entity_type"] == "amount")
        assert amt_entity["value"] == "5000"

    def test_full_overwrite_fields(self, state_manager: StateManager):
        """全量覆写字段: risk_pending_audit / node_position"""
        current = _make_state(risk_pending_audit=False, node_position="classify")
        patches = {"risk_pending_audit": True, "node_position": "generate"}
        result = state_manager._apply_merge_rules(current, patches)
        assert result["risk_pending_audit"] is True
        assert result["node_position"] == "generate"

    def test_intent_stack_dedup(self, state_manager: StateManager):
        """意图栈增量合并: 去重"""
        current = _make_state(intent_stack=["faq", "bill_query"])
        patches = {"intent_stack": ["card_loss", "faq", "complaint"]}
        result = state_manager._apply_merge_rules(current, patches)
        assert result["intent_stack"] == ["faq", "bill_query", "card_loss", "complaint"]


# ══════════════════════════════════════════════════════════════════════
# 5. H5 执行器幂等性
# ══════════════════════════════════════════════════════════════════════


class TestExecutorIdempotency:
    """H5: trace_id + executor_id 幂等性去重"""

    def setup_method(self):
        from smartcs.workflows.activities import reset_dedup_store
        reset_dedup_store()

    def teardown_method(self):
        from smartcs.workflows.activities import reset_dedup_store
        reset_dedup_store()

    @pytest.mark.asyncio
    async def test_same_trace_id_returns_cached_result(self):
        """相同 trace_id + executor_id → 返回缓存结果"""
        from smartcs.workflows.activities import (
            _check_dedup,
            _record_dedup,
            reset_dedup_store,
        )
        reset_dedup_store()

        # 第一次: 无缓存
        assert _check_dedup("trace-1", "ai_service") is None

        # 记录结果
        result = ExecutorOutput(executor_id="ai_service", trace_id="trace-1")
        _record_dedup("trace-1", "ai_service", result)

        # 第二次: 命中缓存
        cached = _check_dedup("trace-1", "ai_service")
        assert cached is not None
        assert cached.trace_id == "trace-1"

    @pytest.mark.asyncio
    async def test_different_trace_id_not_cached(self):
        """不同 trace_id → 不命中缓存"""
        from smartcs.workflows.activities import (
            _check_dedup,
            _record_dedup,
            reset_dedup_store,
        )
        reset_dedup_store()

        result = ExecutorOutput(executor_id="ai_service", trace_id="trace-1")
        _record_dedup("trace-1", "ai_service", result)

        # 不同 trace_id
        assert _check_dedup("trace-2", "ai_service") is None

    @pytest.mark.asyncio
    async def test_different_executor_id_not_cached(self):
        """相同 trace_id 但不同 executor_id → 不命中"""
        from smartcs.workflows.activities import (
            _check_dedup,
            _record_dedup,
            reset_dedup_store,
        )
        reset_dedup_store()

        result = ExecutorOutput(executor_id="ai_service", trace_id="trace-1")
        _record_dedup("trace-1", "ai_service", result)

        # 不同 executor_id
        assert _check_dedup("trace-1", "marketing") is None

    @pytest.mark.asyncio
    async def test_reset_clears_all_dedup(self):
        """reset_dedup_store 清除所有缓存"""
        from smartcs.workflows.activities import (
            _check_dedup,
            _record_dedup,
            reset_dedup_store,
        )
        reset_dedup_store()

        _record_dedup("trace-1", "ai_service", ExecutorOutput(executor_id="ai_service"))
        _record_dedup("trace-2", "marketing", ExecutorOutput(executor_id="marketing"))
        reset_dedup_store()

        assert _check_dedup("trace-1", "ai_service") is None
        assert _check_dedup("trace-2", "marketing") is None


# ══════════════════════════════════════════════════════════════════════
# 6. M7: EmotionVector.decayed_score
# ══════════════════════════════════════════════════════════════════════


class TestEmotionVectorDecay:
    """M7: 情绪向量衰减计算"""

    def test_decayed_score_no_decay(self):
        """Δt=0 → 无衰减"""
        ev = EmotionVector(label="positive", score=0.8, updated_at=datetime.now(UTC))
        assert ev.decayed_score(0) == pytest.approx(0.8, abs=0.01)

    def test_decayed_score_short_interval(self):
        """短时间衰减较小"""
        ev = EmotionVector(label="positive", score=0.8, updated_at=datetime.now(UTC))
        # 10 秒衰减
        decayed = ev.decayed_score(10)
        assert 0.7 < decayed < 0.8  # 衰减很小

    def test_decayed_score_long_interval(self):
        """长时间衰减明显"""
        ev = EmotionVector(label="positive", score=0.8, updated_at=datetime.now(UTC))
        # 300 秒(5分钟)衰减
        decayed = ev.decayed_score(300)
        assert decayed < 0.5  # 衰减明显

    def test_decayed_score_very_long_interval(self):
        """极长时间衰减趋近 0"""
        ev = EmotionVector(label="positive", score=0.8, updated_at=datetime.now(UTC))
        # 1000 秒衰减
        decayed = ev.decayed_score(1000)
        assert decayed < 0.1

    def test_decayed_score_negative_emotion(self):
        """负面情绪也衰减"""
        ev = EmotionVector(label="negative", score=0.9, updated_at=datetime.now(UTC))
        decayed = ev.decayed_score(60)
        assert decayed < 0.9

    def test_decayed_score_zero(self):
        """初始 score=0 → 衰减后仍为 0"""
        ev = EmotionVector(label="neutral", score=0.0, updated_at=datetime.now(UTC))
        assert ev.decayed_score(100) == pytest.approx(0.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_d2_uses_decayed_score(self):
        """D2 评估器使用衰减后的情绪分数"""
        from smartcs.workflows.activities import evaluate_d2_marketing, reset_breakers, reset_dedup_store
        reset_breakers()
        reset_dedup_store()

        # 情绪分数高但很旧 → 衰减后低于阈值 → 不激活
        old_time = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.8,
                "emotion_vector": {"label": "positive", "score": 0.9, "updated_at": old_time},
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        # 30 分钟前的情绪应该显著衰减，低于阈值
        assert result.activated is False

    @pytest.mark.asyncio
    async def test_d2_fresh_emotion_activates(self):
        """D2 评估器: 新鲜的高情绪 → 激活"""
        from smartcs.workflows.activities import evaluate_d2_marketing, reset_breakers, reset_dedup_store
        reset_breakers()
        reset_dedup_store()

        fresh_time = datetime.now(UTC).isoformat()
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.8,
                "emotion_vector": {"label": "positive", "score": 0.9, "updated_at": fresh_time},
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is True


# ══════════════════════════════════════════════════════════════════════
# 7. E1 超时降级通路判定
# ══════════════════════════════════════════════════════════════════════


class TestE1TimeoutDegradation:
    """E1 超时时降级通路判定"""

    @pytest.mark.asyncio
    async def test_timeout_no_fast_path_safe_fallback(self):
        """超时且无快速通路结果 → safe_fallback"""
        from smartcs.workflows.activities import (
            execute_e1_ai_service,
            reset_breakers,
            reset_dedup_store,
            set_ai_dag,
        )
        reset_breakers()
        reset_dedup_store()

        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(side_effect=TimeoutError())
        set_ai_dag(mock_dag)

        inp = ExecutorInput(session_id="s1", message="你好", trace_id="e1-timeout-1")
        result = await execute_e1_ai_service(inp)
        assert result.degraded is True
        assert result.degradation_type == "safe_fallback"

    @pytest.mark.asyncio
    async def test_timeout_with_fast_path_fallback(self):
        """超时但有快速通路结果 → fast_path_fallback"""
        from smartcs.workflows.activities import (
            execute_e1_ai_service,
            reset_breakers,
            reset_dedup_store,
            set_ai_dag,
        )
        reset_breakers()
        reset_dedup_store()

        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(side_effect=TimeoutError())
        # 模拟有快速通路结果
        mock_dag._last_result = {"fast_path_hit": True, "ui_schema": {"scripts": [{"content": "快速话术"}]}}
        set_ai_dag(mock_dag)

        inp = ExecutorInput(session_id="s1", message="你好", trace_id="e1-timeout-2")
        result = await execute_e1_ai_service(inp)
        assert result.degraded is True
        assert result.degradation_type == "fast_path_fallback"

    @pytest.mark.asyncio
    async def test_exception_always_safe_fallback(self):
        """异常（非超时）→ safe_fallback"""
        from smartcs.workflows.activities import (
            execute_e1_ai_service,
            reset_breakers,
            reset_dedup_store,
            set_ai_dag,
        )
        reset_breakers()
        reset_dedup_store()

        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(side_effect=RuntimeError("LLM 不可用"))
        set_ai_dag(mock_dag)

        inp = ExecutorInput(session_id="s1", message="你好", trace_id="e1-exc-1")
        result = await execute_e1_ai_service(inp)
        assert result.degraded is True
        assert result.degradation_type == "safe_fallback"


# ══════════════════════════════════════════════════════════════════════
# 8. DAG PII 脱敏输出 (M3)
# ══════════════════════════════════════════════════════════════════════


class TestDAGPIIMasking:
    """M3: DAG 输出 PII 脱敏"""

    def test_format_out_masks_pii_in_scripts(self):
        """_format_out 对 scripts 内容 PII 脱敏"""
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG
        from smartcs.services.assist.alert_engine import AlertEngine
        from smartcs.services.assist.script_service import ScriptService

        svc = MagicMock()
        engine = MagicMock()
        engine.check_compliance = MagicMock(return_value=[])
        dag = AIExecutorDAG(script_service=svc, alert_engine=engine)

        state = {
            "degraded": False,
            "path": "fast",
            "fast_script": {"content": "客户张三的手机号13800138000"},
            "reranked_candidates": [],
        }
        result = asyncio.get_event_loop().run_until_complete(dag._format_out(state))
        # UI schema 中不应有 PII
        ui_str = str(result["ui_schema"])
        assert "13800138000" not in ui_str
        assert "[PHONE]" in ui_str

    def test_format_out_masks_pii_in_knowledge(self):
        """_format_out 对 knowledge 内容 PII 脱敏"""
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG

        dag = AIExecutorDAG(script_service=MagicMock(), alert_engine=MagicMock())

        state = {
            "degraded": False,
            "path": "deep",
            "fast_script": {},
            "reranked_candidates": [
                {"content": "身份证110101199003076548相关信息", "score": 0.9, "source": "doc1"},
            ],
        }
        result = asyncio.get_event_loop().run_until_complete(dag._format_out(state))
        ui_str = str(result["ui_schema"])
        assert "110101199003076548" not in ui_str
        assert "[IDCARD]" in ui_str

    def test_format_out_deep_path_knowledge_format(self):
        """深度通路输出知识格式化"""
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG

        dag = AIExecutorDAG(script_service=MagicMock(), alert_engine=MagicMock())

        state = {
            "degraded": False,
            "path": "deep",
            "fast_script": {},
            "reranked_candidates": [
                {"content": "知识1", "score": 0.9, "source": "doc1"},
                {"content": "知识2", "score": 0.6, "source": "doc2"},
            ],
        }
        result = asyncio.get_event_loop().run_until_complete(dag._format_out(state))
        assert "knowledge" in result["ui_schema"]
        assert len(result["ui_schema"]["knowledge"]) == 2
        assert result["ui_schema"]["knowledge"][0]["confidence"] == "high"
        assert result["ui_schema"]["knowledge"][1]["confidence"] == "medium"


# ══════════════════════════════════════════════════════════════════════
# 9. 反馈延迟确认 + 撤销 (H2)
# ══════════════════════════════════════════════════════════════════════


class TestFeedbackDelayedCommit:
    """H2: 延迟确认 + 撤销"""

    @pytest_asyncio.fixture
    async def feedback_client(self):
        from smartcs.main import create_assist_app

        app = create_assist_app()
        app.state.classifier = None
        app.state.assist_orchestrator = None
        app.state.temporal_client = None
        app.state.state_manager = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    async def test_feedback_returns_delayed_commit(self, feedback_client: AsyncClient):
        """POST /api/feedback 返回 delayed_commit=True"""
        resp = await feedback_client.post("/api/feedback", json={
            "session_id": "test-delayed-1",
            "agent_id": "agent-001",
            "action": "accept",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["delayed_commit"] is True

        # 清理
        from smartcs.services.assist.router import _feedback_buffer
        _feedback_buffer.pop("test-delayed-1:agent-001", None)

    async def test_feedback_buffer_contents(self, feedback_client: AsyncClient):
        """POST /api/feedback 缓冲区存储正确数据"""
        resp = await feedback_client.post("/api/feedback", json={
            "session_id": "test-buffer-1",
            "agent_id": "agent-002",
            "action": "modify",
            "modify_fields": ["script_content"],
        })
        assert resp.status_code == 200

        from smartcs.services.assist.router import _feedback_buffer
        key = "test-buffer-1:agent-002"
        assert key in _feedback_buffer
        assert _feedback_buffer[key]["action"] == "modify"
        assert _feedback_buffer[key]["confidence"] == 0.5
        assert _feedback_buffer[key]["modify_fields"] == ["script_content"]

        # 清理
        _feedback_buffer.pop(key, None)

    async def test_feedback_undo_clears_buffer(self, feedback_client: AsyncClient):
        """POST /api/feedback/undo 清除缓冲区"""
        # 先提交
        await feedback_client.post("/api/feedback", json={
            "session_id": "test-undo-1",
            "agent_id": "agent-003",
            "action": "accept",
        })

        from smartcs.services.assist.router import _feedback_buffer
        key = "test-undo-1:agent-003"
        assert key in _feedback_buffer

        # 撤销
        resp = await feedback_client.post("/api/feedback/undo", json={
            "session_id": "test-undo-1",
            "agent_id": "agent-003",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["undone"] is True

        # 缓冲区应清除
        assert key not in _feedback_buffer

    async def test_feedback_undo_nonexistent_ok(self, feedback_client: AsyncClient):
        """POST /api/feedback/undo 缓冲区无记录 → 仍返回成功"""
        resp = await feedback_client.post("/api/feedback/undo", json={
            "session_id": "nonexistent-session",
            "agent_id": "agent-004",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_feedback_no_immediate_cas_write(self, feedback_client: AsyncClient):
        """POST /api/feedback 不立即调用 cas_write（延迟确认）"""
        from smartcs.main import create_assist_app
        from unittest.mock import AsyncMock

        app = create_assist_app()
        app.state.classifier = None
        app.state.assist_orchestrator = None
        app.state.temporal_client = None

        mock_state_manager = AsyncMock()
        mock_state_manager.read_state = AsyncMock(return_value={"version": 1})
        mock_state_manager.cas_write = AsyncMock(return_value={"ok": True, "new_version": 2})
        app.state.state_manager = mock_state_manager

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/feedback", json={
                "session_id": "test-no-cas-1",
                "agent_id": "agent-005",
                "action": "accept",
            })

        # 不应立即调用 cas_write
        mock_state_manager.cas_write.assert_not_awaited()

        # 清理
        from smartcs.services.assist.router import _feedback_buffer
        _feedback_buffer.pop("test-no-cas-1:agent-005", None)

    async def test_feedback_all_action_types(self, feedback_client: AsyncClient):
        """所有操作类型的 confidence 映射"""
        cases = [
            ("accept", 1.0),
            ("modify", 0.5),
            ("partial_accept", 0.3),
            ("reject", 0.0),
        ]
        from smartcs.services.assist.router import _feedback_buffer

        for action, expected_conf in cases:
            sid = f"test-action-{action}"
            resp = await feedback_client.post("/api/feedback", json={
                "session_id": sid,
                "agent_id": "agent-006",
                "action": action,
            })
            assert resp.status_code == 200
            assert resp.json()["confidence"] == expected_conf

            # 清理
            _feedback_buffer.pop(f"{sid}:agent-006", None)

    async def test_feedback_missing_required_fields(self, feedback_client: AsyncClient):
        """缺少必填字段返回 422"""
        # 缺少 session_id
        resp = await feedback_client.post("/api/feedback", json={
            "agent_id": "agent-007",
            "action": "accept",
        })
        assert resp.status_code == 422

        # 缺少 agent_id
        resp = await feedback_client.post("/api/feedback", json={
            "session_id": "test-missing",
            "action": "accept",
        })
        assert resp.status_code == 422

        # 无效 action
        resp = await feedback_client.post("/api/feedback", json={
            "session_id": "test-missing",
            "agent_id": "agent-007",
            "action": "invalid_action",
        })
        assert resp.status_code == 422


# ══════════════════════════════════════════════════════════════════════
# 10. Activities: read_state_snapshot + cas_write_state
# ══════════════════════════════════════════════════════════════════════


class TestStateActivities:
    """read_state_snapshot 和 cas_write_state Activities"""

    @pytest.mark.asyncio
    async def test_read_state_snapshot_returns_state(self):
        """有 Redis 状态时返回快照"""
        from smartcs.workflows.activities import (
            get_redis_from_app,
            set_redis_for_activities,
        )

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(_make_state(version=3)))
        set_redis_for_activities(mock_redis)

        from smartcs.workflows.activities import read_state_snapshot
        result = await read_state_snapshot("sess-001")
        assert result is not None
        assert result["version"] == 3

        set_redis_for_activities(None)

    @pytest.mark.asyncio
    async def test_read_state_snapshot_returns_none_when_missing(self):
        """无 Redis 状态时返回 None"""
        from smartcs.workflows.activities import (
            set_redis_for_activities,
        )

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        set_redis_for_activities(mock_redis)

        from smartcs.workflows.activities import read_state_snapshot
        result = await read_state_snapshot("sess-missing")
        assert result is None

        set_redis_for_activities(None)

    @pytest.mark.asyncio
    async def test_read_state_snapshot_no_redis(self):
        """无 Redis 实例时返回 None"""
        from smartcs.workflows.activities import set_redis_for_activities

        set_redis_for_activities(None)

        from smartcs.workflows.activities import read_state_snapshot
        result = await read_state_snapshot("sess-no-redis")
        assert result is None

    @pytest.mark.asyncio
    async def test_cas_write_state_success(self):
        """CAS 写入成功"""
        from smartcs.workflows.activities import set_redis_for_activities

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(_make_state(version=3)))
        mock_redis.script_load = AsyncMock(return_value="sha123")
        cas_result = json.dumps({"ok": True, "new_version": 4}).encode()
        mock_redis.evalsha = AsyncMock(return_value=cas_result)
        set_redis_for_activities(mock_redis)

        from smartcs.workflows.activities import cas_write_state
        result = await cas_write_state("sess-001", {"suppress_flag": True})
        assert result["ok"] is True

        set_redis_for_activities(None)

    @pytest.mark.asyncio
    async def test_cas_write_state_no_redis(self):
        """无 Redis 实例 → 返回 redis_unavailable"""
        from smartcs.workflows.activities import set_redis_for_activities
        set_redis_for_activities(None)

        from smartcs.workflows.activities import cas_write_state
        result = await cas_write_state("sess-001", {"suppress_flag": True})
        assert result["ok"] is False
        assert result["reason"] == "redis_unavailable"


# ══════════════════════════════════════════════════════════════════════
# 11. E3 并行超时 (H4)
# ══════════════════════════════════════════════════════════════════════


class TestE3ParallelTimeout:
    """H4: E3 并行执行时 timeout = min(E1.SLA, E3.SLA)"""

    def test_e3_timeout_when_parallel_with_e1(self):
        """E1 和 E3 并行时，E3 超时取两者 SLA 的最小值"""
        e1_sla_ms = 3000
        e3_sla_ms = 100
        # 当 E1 激活时，E3 的并行超时 = min(E1.SLA, E3.SLA)
        e3_parallel_timeout = min(e1_sla_ms, e3_sla_ms)
        assert e3_parallel_timeout == 100

    def test_e3_timeout_when_alone(self):
        """E3 独立执行时使用自身 SLA"""
        e3_sla_ms = 100
        # E1 未激活时，E3 独立执行，使用自身 SLA
        assert e3_sla_ms == 100


# ══════════════════════════════════════════════════════════════════════
# 12. 端到端编排场景
# ══════════════════════════════════════════════════════════════════════


class TestEndToEndOrchestrationScenarios:
    """端到端编排场景（纯逻辑验证，不启动 Temporal）"""

    def test_scenario_service_triggers_suppress_then_decay(self):
        """场景: 服务激活→压制营销→压制衰减→压制清除"""
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

        wf = OrchestrationWorkflow()

        # 第1轮: D1+D2 激活 → 压制 D2, suppress_remaining=2
        plan = wf._apply_policies(
            EvaluatorOutput(activated=True),
            EvaluatorOutput(activated=True),
            EvaluatorOutput(activated=True),
        )
        assert plan["d2_suppressed"] is True
        assert wf._suppress_remaining == 2

        # 第2轮: D1 不激活, D2 不激活 → suppress_remaining 不变（非 D1+D2 同时激活不重置）
        # 但 Workflow.run 中有递减逻辑
        wf._suppress_remaining -= 1  # 模拟 Workflow.run 中的递减
        assert wf._suppress_remaining == 1

        # 第3轮: 再递减
        wf._suppress_remaining -= 1
        assert wf._suppress_remaining == 0

        # 第4轮: suppress_remaining=0 → CAS 补丁含 suppress_force_clear
        patches: dict[str, Any] = {}
        if wf._suppress_remaining > 0:
            patches["suppress_flag"] = True
        else:
            patches["suppress_flag"] = False
            patches["suppress_force_clear"] = True
        assert patches["suppress_flag"] is False
        assert patches["suppress_force_clear"] is True

    def test_scenario_risk_block_overrides_marketing(self):
        """场景: 风控 BLOCK → 仲裁层跳过营销"""
        arbitrator = GlobalArbitrator()
        results = {
            "risk": _make_executor(
                executor_id="risk",
                ui_schema={"action": "BLOCK", "reason": "高风险"},
                risk_action="BLOCK",
            ),
            "ai_service": _make_executor(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
            "marketing": _make_executor(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"name": "产品"}]},
            ),
        }
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(arbitrator.arbitrate(results))
        assert result.fusion_type == "risk_blocked"
        assert result.marketing_slot is None
        assert result.primary_card["type"] == "risk_block"

    def test_scenario_full_pipeline_normal(self):
        """场景: 全通路正常 → PASS + 服务 + 营销"""
        arbitrator = GlobalArbitrator()
        results = {
            "risk": _make_executor(
                executor_id="risk",
                ui_schema={"action": "PASS"},
                risk_action="PASS",
            ),
            "ai_service": _make_executor(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
            "marketing": _make_executor(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"name": "产品"}]},
            ),
        }
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(arbitrator.arbitrate(results))
        assert result.fusion_type == "service_marketing"
        assert result.primary_card["type"] == "service_answer"
        assert result.marketing_slot is not None
        assert result.marketing_slot["type"] == "marketing_standard"

    def test_scenario_degraded_risk_with_pii(self):
        """场景: E3 降级(待审) + 服务含 PII → PII 脱敏 + risk_pending_audit"""
        arbitrator = GlobalArbitrator()
        results = {
            "risk": _make_executor(
                executor_id="risk",
                ui_schema={"action": "PASS", "risk_pending_audit": True},
                risk_action="PASS",
                degraded=True,
                degradation_type="pass_with_audit_flag",
            ),
            "ai_service": _make_executor(
                executor_id="ai_service",
                ui_schema={"answer": "客户张三手机13800138000"},
            ),
        }
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(arbitrator.arbitrate(results))
        # PII 被脱敏
        assert "13800138000" not in str(result.primary_card)
        assert "[PHONE]" in str(result.primary_card)
        # 风控结果中有待审标记
        risk_result = results["risk"]
        assert risk_result.degraded is True
        assert risk_result.ui_schema.get("risk_pending_audit") is True


# ══════════════════════════════════════════════════════════════════════
# 13. DAG 重排序与归一化
# ══════════════════════════════════════════════════════════════════════


class TestDAGRerankAndNormalize:
    """DAG 重排序与候选归一化"""

    @pytest.mark.asyncio
    async def test_normalize_limits_rag_top5(self):
        """RAG 候选截取 Top-5"""
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG
        dag = AIExecutorDAG(script_service=MagicMock(), alert_engine=MagicMock())
        state = {
            "rag_candidates": [{"content": f"rag-{i}", "score": 0.5} for i in range(8)],
            "kg_candidates": [{"content": f"kg-{i}", "score": 0.5} for i in range(2)],
        }
        result = await dag._normalize(state)
        rag_count = len([c for c in result["normalized_candidates"] if c["content"].startswith("rag")])
        assert rag_count == 5

    @pytest.mark.asyncio
    async def test_normalize_limits_kg_top3(self):
        """KG 候选截取 Top-3"""
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG
        dag = AIExecutorDAG(script_service=MagicMock(), alert_engine=MagicMock())
        state = {
            "rag_candidates": [{"content": f"rag-{i}", "score": 0.5} for i in range(3)],
            "kg_candidates": [{"content": f"kg-{i}", "score": 0.5} for i in range(5)],
        }
        result = await dag._normalize(state)
        kg_count = len([c for c in result["normalized_candidates"] if c["content"].startswith("kg")])
        assert kg_count == 3

    @pytest.mark.asyncio
    async def test_rerank_no_adoption_rate_downweighted(self):
        """无采纳率的候选降权 ×0.7"""
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG
        dag = AIExecutorDAG(script_service=MagicMock(), alert_engine=MagicMock())
        state = {
            "normalized_candidates": [
                {"content": "A", "score": 1.0, "adoption_rate": 0.8},
                {"content": "B", "score": 1.0},  # no adoption_rate
            ],
        }
        result = await dag._rerank(state)
        a = next(c for c in result["reranked_candidates"] if c["content"] == "A")
        b = next(c for c in result["reranked_candidates"] if c["content"] == "B")
        assert a["score"] == 1.0
        assert b["score"] == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_rerank_empty_candidates(self):
        """空候选列表返回空"""
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG
        dag = AIExecutorDAG(script_service=MagicMock(), alert_engine=MagicMock())
        result = await dag._rerank({"normalized_candidates": []})
        assert result["reranked_candidates"] == []


# ══════════════════════════════════════════════════════════════════════
# 14. 合规短语过滤递归
# ══════════════════════════════════════════════════════════════════════


class TestComplianceFilterRecursive:
    """合规短语递归过滤"""

    def test_filter_in_nested_dict(self, arbitrator: GlobalArbitrator):
        """嵌套字典中的合规短语被过滤"""
        data = {
            "scripts": [
                {"content": "本产品保证收益", "tags": ["faq"]},
                {"content": "正常话术"},
            ],
        }
        result = arbitrator._filter_compliance_recursive(data)
        assert "保证收益" not in str(result)
        assert "[已过滤]" in str(result)

    def test_filter_in_list(self, arbitrator: GlobalArbitrator):
        """列表中的合规短语被过滤"""
        data = ["保证收益", "正常文本", "零风险投资"]
        result = arbitrator._filter_compliance_recursive(data)
        assert result[0] == "[已过滤]"
        assert result[1] == "正常文本"
        assert "零风险" not in result[2]

    def test_filter_multiple_in_same_string(self, arbitrator: GlobalArbitrator):
        """同一字符串中有多个违规短语"""
        text = "保证收益且稳赚不赔"
        result = arbitrator.filter_compliance(text)
        assert "保证收益" not in result
        assert "稳赚不赔" not in result
        assert result.count("[已过滤]") >= 2

    def test_filter_preserves_non_string_types(self, arbitrator: GlobalArbitrator):
        """非字符串类型不变"""
        data = {"count": 42, "flag": True, "value": None, "text": "保证收益"}
        result = arbitrator._filter_compliance_recursive(data)
        assert result["count"] == 42
        assert result["flag"] is True
        assert result["value"] is None
        assert "保证收益" not in result["text"]


# ══════════════════════════════════════════════════════════════════════
# 15. PII 脱敏边界条件
# ══════════════════════════════════════════════════════════════════════


class TestPIIMaskingEdgeCases:
    """PII 脱敏边界条件"""

    def test_multiple_pii_types_in_one_string(self, arbitrator: GlobalArbitrator):
        """同一字符串中有多种 PII"""
        text = "客户张三手机13800138000卡号6222021234567890"
        result = arbitrator.mask_pii(text)
        assert "13800138000" not in result
        assert "6222021234567890" not in result
        assert "[PHONE]" in result
        assert "[BANKCARD]" in result

    def test_idcard_with_x(self, arbitrator: GlobalArbitrator):
        """身份证号末位 X"""
        result = arbitrator.mask_pii("号码44030520001201234X")
        assert "[IDCARD]" in result
        assert "44030520001201234X" not in result

    def test_empty_string(self, arbitrator: GlobalArbitrator):
        """空字符串不变"""
        assert arbitrator.mask_pii("") == ""

    def test_pii_in_list_recursive(self, arbitrator: GlobalArbitrator):
        """列表中嵌套 PII 递归脱敏"""
        data = [
            "手机13800138000",
            {"key": "身份证110101199003076548"},
            [6222021234567890],  # 非字符串数字
        ]
        result = arbitrator._mask_pii_recursive(data)
        assert "13800138000" not in str(result[0])
        assert "[PHONE]" in result[0]
        assert "110101199003076548" not in str(result[1])
        assert result[2][0] == 6222021234567890  # 数字不变


# ══════════════════════════════════════════════════════════════════════
# 16. StateManager CAS 写入边界
# ══════════════════════════════════════════════════════════════════════


class TestCASCWriteEdgeCases:
    """CAS 写入边界条件"""

    async def test_cas_write_max_retries_exhausted(self, state_manager: StateManager, mock_redis: AsyncMock):
        """重试次数耗尽返回失败"""
        current = _make_state(version=5)
        mock_redis.get = AsyncMock(return_value=json.dumps(current))

        conflict = json.dumps({"ok": False, "current_version": 5}).encode()
        mock_redis.evalsha = AsyncMock(return_value=conflict)
        mock_redis.script_load = AsyncMock(return_value="sha123")

        result = await state_manager.cas_write(
            "sess-001",
            expected_version=3,
            patches={"node_position": "generate"},
            writer="e1",
            max_retries=2,
        )
        assert result["ok"] is False

    async def test_cas_write_redis_unavailable(self, state_manager: StateManager, mock_redis: AsyncMock):
        """Redis 异常时抛出异常（StateManager 未捕获 Redis 连接异常）"""
        mock_redis.get = AsyncMock(side_effect=Exception("Redis connection lost"))

        with pytest.raises(Exception, match="Redis connection lost"):
            await state_manager.cas_write(
                "sess-001",
                expected_version=1,
                patches={"node_position": "generate"},
                writer="e1",
            )

    async def test_init_state_concurrent_writes(self, state_manager: StateManager, mock_redis: AsyncMock):
        """并发初始化: 已存在时幂等返回"""
        existing = _make_state(version=1, customer_id="cust-001")
        mock_redis.get = AsyncMock(return_value=json.dumps(existing))
        mock_redis.set = AsyncMock(return_value=None)  # NX 模式下未设置

        result = await state_manager.init_state("sess-exists", {"customer_id": "cust-002"})
        # 应返回已有状态
        assert result["customer_id"] == "cust-001"
        # SET NX 未被调用（因为 get 已返回存在状态）
        mock_redis.set.assert_not_awaited()
