"""编排引擎 Workflow 单元测试

测试纯逻辑方法 _apply_policies 和 _arbitrate。
Workflow 完整流程需要 Temporal Server，在集成测试中覆盖。
"""
from __future__ import annotations

import pytest

from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow
from smartcs.workflows.shared import EvaluatorOutput, ExecutorOutput, OrchestrationResult


@pytest.fixture
def workflow():
    return OrchestrationWorkflow()


class TestApplyPolicies:
    """编排策略矩阵"""

    def test_service_suppresses_marketing(self, workflow):
        """策略1: D1 激活时压制 D2"""
        d1 = EvaluatorOutput(activated=True)
        d2 = EvaluatorOutput(activated=True)
        d3 = EvaluatorOutput(activated=True)
        plan = workflow._apply_policies(d1, d2, d3)
        assert plan["d2_suppressed"] is True

    def test_service_alone_does_not_suppress(self, workflow):
        """D1 激活但 D2 未激活时，不触发压制"""
        d1 = EvaluatorOutput(activated=True)
        d2 = EvaluatorOutput(activated=False)
        d3 = EvaluatorOutput(activated=True)
        plan = workflow._apply_policies(d1, d2, d3)
        assert plan["d2_suppressed"] is False

    def test_marketing_alone_not_suppressed(self, workflow):
        """D2 激活但 D1 未激活时，不触发压制"""
        d1 = EvaluatorOutput(activated=False)
        d2 = EvaluatorOutput(activated=True)
        d3 = EvaluatorOutput(activated=True)
        plan = workflow._apply_policies(d1, d2, d3)
        assert plan["d2_suppressed"] is False

    def test_no_activation_no_suppression(self, workflow):
        d1 = EvaluatorOutput(activated=False)
        d2 = EvaluatorOutput(activated=False)
        d3 = EvaluatorOutput(activated=True)
        plan = workflow._apply_policies(d1, d2, d3)
        assert plan["d2_suppressed"] is False


class TestArbitrate:
    """优先级融合展示规则（§3.5）"""

    def test_risk_block_fusion(self, workflow):
        """风控 BLOCK: 主卡片=风控拦截, 营销=不展示"""
        results = {
            "risk": ExecutorOutput(
                executor_id="risk",
                risk_action="BLOCK",
                ui_schema={"action": "BLOCK", "reason": "高风险"},
            ),
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
            "marketing": ExecutorOutput(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"name": "产品"}]},
            ),
        }
        result = workflow._arbitrate(results)
        assert result.fusion_type == "risk_blocked"
        assert result.marketing_slot is None
        assert result.primary_card["type"] == "risk_block"

    def test_risk_warn_fusion(self, workflow):
        """风控 WARN: 主卡片=服务, 风险标记=徽章, 营销=降级小卡片"""
        results = {
            "risk": ExecutorOutput(
                executor_id="risk",
                risk_action="WARN",
                ui_schema={"action": "WARN", "alerts": [{"level": "warning", "message": "注意"}]},
            ),
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
            "marketing": ExecutorOutput(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"name": "产品"}]},
            ),
        }
        result = workflow._arbitrate(results)
        assert result.fusion_type == "service_risk_warn"
        assert result.risk_badge is not None
        assert result.risk_badge["type"] == "risk_badge"
        assert result.marketing_slot is not None
        assert result.marketing_slot["type"] == "marketing_small"

    def test_risk_pass_fusion(self, workflow):
        """风控 PASS: 主卡片=服务, 营销=标准展示"""
        results = {
            "risk": ExecutorOutput(
                executor_id="risk",
                risk_action="PASS",
                ui_schema={"action": "PASS"},
            ),
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
            "marketing": ExecutorOutput(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"name": "产品"}]},
            ),
        }
        result = workflow._arbitrate(results)
        assert result.fusion_type == "service_marketing"
        assert result.marketing_slot is not None
        assert result.marketing_slot["type"] == "marketing_standard"

    def test_service_only_fusion(self, workflow):
        """只有服务结果，无营销"""
        results = {
            "risk": ExecutorOutput(
                executor_id="risk",
                risk_action="PASS",
                ui_schema={"action": "PASS"},
            ),
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
        }
        result = workflow._arbitrate(results)
        assert result.fusion_type == "service_only"
        assert result.marketing_slot is None

    def test_empty_results(self, workflow):
        """无执行结果"""
        result = workflow._arbitrate({})
        assert result.fusion_type == "service_only"

    def test_risk_only_block(self, workflow):
        """只有风控 BLOCK 结果"""
        results = {
            "risk": ExecutorOutput(
                executor_id="risk",
                risk_action="BLOCK",
                ui_schema={"action": "BLOCK"},
            ),
        }
        result = workflow._arbitrate(results)
        assert result.fusion_type == "risk_blocked"
        assert result.marketing_slot is None
