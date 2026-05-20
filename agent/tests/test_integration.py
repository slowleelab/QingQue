"""AI 辅助功能集成测试

跨模块协作验证：路由→编排→执行→仲裁→反馈全链路集成。
不依赖真实中间件（Redis/ES/Milvus/Temporal），使用 mock 注入替换外部依赖，
验证各模块之间的接口契约、数据流转、降级策略和边界行为。

测试层次:
- API 层: FastAPI TestClient → 路由 → mock 依赖
- 编排层: 评估器→策略→执行器→仲裁 全链路
- 状态层: CAS 写回 → StateManager 合并规则
- 反馈层: HTTP 端点 → 缓冲区 → 延迟提交
- DAG 层: 脚本检索→快速/深度通路→防火墙→输出
- 仲裁层: 多执行器结果融合 + PII/合规 全链路
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

from smartcs.main import create_assist_app
from smartcs.services.assist.arbitrator import GlobalArbitrator
from smartcs.services.assist.router import WS_POOL_KEY, _feedback_buffer
from smartcs.shared.middleware import register_exception_handlers
from smartcs.shared.models import IntentLabel, SentimentLabel
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


@pytest_asyncio.fixture
async def assist_app():
    """创建最小化的 Assist FastAPI 应用，注入 mock 依赖"""
    app = create_assist_app()
    register_exception_handlers(app)
    # 禁用所有外部依赖
    app.state.classifier = None
    app.state.assist_orchestrator = None
    app.state.temporal_client = None
    app.state.state_manager = None
    app.state.session_manager = None
    app.state.assist_ws_connections = {}
    yield app


@pytest_asyncio.fixture
async def client(assist_app):
    """创建 httpx AsyncClient"""
    transport = ASGITransport(app=assist_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def client_with_state_manager(assist_app):
    """创建带 mock StateManager 的 Client"""
    mock_sm = AsyncMock()
    mock_sm.read_state = AsyncMock(return_value={
        "version": 3,
        "last_confidence": 0.8,
        "intent_stack": ["faq"],
    })
    mock_sm.cas_write = AsyncMock(return_value={"ok": True, "new_version": 4})
    assist_app.state.state_manager = mock_sm
    transport = ASGITransport(app=assist_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, mock_sm


@pytest_asyncio.fixture
async def client_with_orchestrator(assist_app):
    """创建带 mock AssistOrchestrator 的 Client（模拟降级路径）"""
    from smartcs.shared.models import (
        AssistPushMessage,
        AssistPushPayload,
        ScriptCard,
    )

    mock_orch = MagicMock()
    push_msg = AssistPushMessage(
        session_id="s1",
        timestamp=datetime.now(UTC),
        trigger="customer_message",
        payload=AssistPushPayload(
            scripts=[ScriptCard(script_id="s1", content="话术", tags=["faq"], priority=5)],
            knowledge=[],
            alerts=[],
            recommendations=[],
        ),
    )
    mock_orch.process = AsyncMock(return_value=push_msg)
    mock_orch.should_throttle = MagicMock(return_value=False)
    assist_app.state.assist_orchestrator = mock_orch
    transport = ASGITransport(app=assist_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, mock_orch


@pytest.fixture(autouse=True)
def _clean_feedback_buffer():
    """每个测试前后清理反馈缓冲区"""
    _feedback_buffer.clear()
    yield
    _feedback_buffer.clear()


# ══════════════════════════════════════════════════════════════════════
# 1. API 健康检查
# ══════════════════════════════════════════════════════════════════════


class TestHealthEndpoint:
    """GET /api/health 集成测试"""

    async def test_health_returns_ok(self, client: AsyncClient):
        """健康检查端点返回 200"""
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "assist"


# ══════════════════════════════════════════════════════════════════════
# 2. Analyze 端点集成
# ══════════════════════════════════════════════════════════════════════


class TestAnalyzeEndpoint:
    """POST /api/analyze 集成测试

    验证: 路由→意图分类(可选)→编排(Temporal/降级)→推送→响应
    """

    async def test_analyze_no_dependencies(self, client: AsyncClient):
        """无任何外部依赖时仍返回 200（空 payload）"""
        resp = await client.post("/api/analyze", json={
            "session_id": "sess-001",
            "message": "查询账单",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        # 无分类器时使用默认 FAQ
        assert data["intent"] == "faq"

    async def test_analyze_with_orchestrator_fallback(self, client_with_orchestrator):
        """Temporal 不可用时降级到 AssistOrchestrator"""
        client, mock_orch = client_with_orchestrator
        resp = await client.post("/api/analyze", json={
            "session_id": "sess-002",
            "message": "分期手续费",
        })
        assert resp.status_code == 200
        # 验证降级到了 orchestrator
        mock_orch.process.assert_awaited_once()

    async def test_analyze_with_classifier(self, assist_app):
        """有分类器时使用分类结果"""
        mock_classifier = MagicMock()
        intent_result = MagicMock()
        intent_result.primary_intent = IntentLabel.BILL_QUERY
        intent_result.primary_confidence = 0.92
        mock_classifier.classify = AsyncMock(
            return_value=(intent_result, [], MagicMock(), "rule")
        )
        assist_app.state.classifier = mock_classifier

        transport = ASGITransport(app=assist_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/analyze", json={
                "session_id": "sess-003",
                "message": "上个月账单多少",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "bill_query"
        assert data["confidence"] == 0.92

    async def test_analyze_classifier_timeout_uses_default(self, assist_app):
        """分类器超时时降级为 FAQ"""
        mock_classifier = MagicMock()
        mock_classifier.classify = AsyncMock(side_effect=TimeoutError())
        assist_app.state.classifier = mock_classifier

        transport = ASGITransport(app=assist_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/analyze", json={
                "session_id": "sess-004",
                "message": "查询",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "faq"

    async def test_analyze_missing_session_id(self, client: AsyncClient):
        """缺少 session_id 返回 422"""
        resp = await client.post("/api/analyze", json={
            "message": "查询",
        })
        assert resp.status_code == 422

    async def test_analyze_missing_message(self, client: AsyncClient):
        """缺少 message 返回 422"""
        resp = await client.post("/api/analyze", json={
            "session_id": "sess-005",
        })
        assert resp.status_code == 422

    async def test_analyze_no_ws_connection_logs_warning(self, client: AsyncClient):
        """无 WebSocket 连接时不报错（仅记日志）"""
        resp = await client.post("/api/analyze", json={
            "session_id": "sess-no-ws",
            "message": "查询",
        })
        # 即使无 WS 连接，HTTP 端点仍返回 200
        assert resp.status_code == 200

    async def test_analyze_pushes_to_websocket(self, assist_app):
        """有 WS 连接时推送结果"""
        # 模拟 WS 连接
        mock_ws = AsyncMock()
        assist_app.state.assist_ws_connections["sess-ws"] = mock_ws
        # 注入 orchestrator
        from smartcs.shared.models import AssistPushMessage, AssistPushPayload
        mock_orch = MagicMock()
        mock_orch.process = AsyncMock(return_value=AssistPushMessage(
            session_id="sess-ws",
            timestamp=datetime.now(UTC),
            trigger="customer_message",
            payload=AssistPushPayload(scripts=[], knowledge=[], alerts=[], recommendations=[]),
        ))
        mock_orch.should_throttle = MagicMock(return_value=False)
        assist_app.state.assist_orchestrator = mock_orch

        transport = ASGITransport(app=assist_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/analyze", json={
                "session_id": "sess-ws",
                "message": "查询",
            })
        assert resp.status_code == 200
        # WS 推送被调用
        mock_ws.send_json.assert_awaited()


# ══════════════════════════════════════════════════════════════════════
# 3. 反馈端点全链路集成
# ══════════════════════════════════════════════════════════════════════


class TestFeedbackIntegration:
    """POST /api/feedback + POST /api/feedback/undo 全链路"""

    async def test_feedback_accept_flow(self, client: AsyncClient):
        """accept 操作: confidence=1.0 + delayed_commit"""
        resp = await client.post("/api/feedback", json={
            "session_id": "sess-fb-1",
            "agent_id": "agent-001",
            "action": "accept",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["action"] == "accept"
        assert data["confidence"] == 1.0
        assert data["delayed_commit"] is True

    async def test_feedback_modify_with_fields(self, client: AsyncClient):
        """modify 操作: confidence=0.5 + modify_fields"""
        resp = await client.post("/api/feedback", json={
            "session_id": "sess-fb-2",
            "agent_id": "agent-001",
            "action": "modify",
            "modify_fields": ["script_content", "knowledge_summary"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["confidence"] == 0.5
        # 缓冲区包含 modify_fields
        key = "sess-fb-2:agent-001"
        assert key in _feedback_buffer
        assert _feedback_buffer[key]["modify_fields"] == ["script_content", "knowledge_summary"]

    async def test_feedback_then_undo(self, client: AsyncClient):
        """提交反馈后 3 秒内撤销"""
        # 提交
        await client.post("/api/feedback", json={
            "session_id": "sess-fb-3",
            "agent_id": "agent-001",
            "action": "reject",
        })
        key = "sess-fb-3:agent-001"
        assert key in _feedback_buffer

        # 撤销
        resp = await client.post("/api/feedback/undo", json={
            "session_id": "sess-fb-3",
            "agent_id": "agent-001",
        })
        assert resp.status_code == 200
        assert resp.json()["undone"] is True
        assert key not in _feedback_buffer

    async def test_undo_without_prior_feedback(self, client: AsyncClient):
        """撤销不存在的反馈返回 undone=False"""
        resp = await client.post("/api/feedback/undo", json={
            "session_id": "nonexistent",
            "agent_id": "agent-001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["undone"] is False
        assert data["reason"] == "not_buffered"

    async def test_feedback_overwrite_in_buffer(self, client: AsyncClient):
        """同一 session+agent 重复提交覆盖缓冲区"""
        # 第一次
        await client.post("/api/feedback", json={
            "session_id": "sess-fb-4",
            "agent_id": "agent-001",
            "action": "reject",
        })
        key = "sess-fb-4:agent-001"
        assert _feedback_buffer[key]["action"] == "reject"

        # 第二次覆盖
        await client.post("/api/feedback", json={
            "session_id": "sess-fb-4",
            "agent_id": "agent-001",
            "action": "accept",
        })
        assert _feedback_buffer[key]["action"] == "accept"
        assert _feedback_buffer[key]["confidence"] == 1.0

    async def test_feedback_delayed_commit_with_state_manager(self, client_with_state_manager):
        """有 StateManager 时反馈不立即提交"""
        client, mock_sm = client_with_state_manager
        resp = await client.post("/api/feedback", json={
            "session_id": "sess-fb-5",
            "agent_id": "agent-001",
            "action": "accept",
        })
        assert resp.status_code == 200
        # H2: 不立即调用 cas_write
        mock_sm.cas_write.assert_not_awaited()

    async def test_feedback_all_actions(self, client: AsyncClient):
        """所有 action 类型的 confidence 映射正确"""
        expected = {
            "accept": 1.0,
            "modify": 0.5,
            "partial_accept": 0.3,
            "reject": 0.0,
        }
        for action, conf in expected.items():
            sid = f"sess-action-{action}"
            resp = await client.post("/api/feedback", json={
                "session_id": sid,
                "agent_id": "agent-001",
                "action": action,
            })
            assert resp.status_code == 200
            assert resp.json()["confidence"] == conf

    async def test_feedback_validation(self, client: AsyncClient):
        """参数校验: 缺少必填/无效值返回 422"""
        # 缺少 session_id
        resp = await client.post("/api/feedback", json={
            "agent_id": "agent-001",
            "action": "accept",
        })
        assert resp.status_code == 422

        # 缺少 agent_id
        resp = await client.post("/api/feedback", json={
            "session_id": "sess-1",
            "action": "accept",
        })
        assert resp.status_code == 422

        # 无效 action
        resp = await client.post("/api/feedback", json={
            "session_id": "sess-1",
            "agent_id": "agent-001",
            "action": "unknown",
        })
        assert resp.status_code == 422


# ══════════════════════════════════════════════════════════════════════
# 4. 评估器→策略→仲裁 全链路
# ══════════════════════════════════════════════════════════════════════


class TestEvaluatorToArbitratorIntegration:
    """评估器结果→策略矩阵→仲裁融合 全链路（不启动 Temporal）"""

    @pytest.mark.asyncio
    async def test_d1_d3_activated_service_with_risk(self):
        """D1+D3 激活 → E1+E3 并行 → 仲裁融合"""
        # 1. 模拟评估结果
        d1 = EvaluatorOutput(activated=True, cooldown_remaining=2)
        d2 = EvaluatorOutput(activated=False)
        d3 = EvaluatorOutput(activated=True, cooldown_remaining=0)

        # 2. 应用策略
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow
        wf = OrchestrationWorkflow()
        plan = wf._apply_policies(d1, d2, d3)
        assert plan["d2_suppressed"] is False  # D2 未激活，不压制

        # 3. 模拟执行器结果
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "您好，请问有什么可以帮您？"}]},
                success=True,
            ),
            "risk": ExecutorOutput(
                executor_id="risk",
                ui_schema={"action": "PASS"},
                risk_action="PASS",
            ),
        }

        # 4. 仲裁融合
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "service_only"
        assert result.primary_card["type"] == "service_answer"

    @pytest.mark.asyncio
    async def test_d1_d2_d3_all_activated_suppress_then_arbitrate(self):
        """D1+D2+D3 全激活 → D2 被压制 → 仲裁无营销"""
        # 1. 评估
        d1 = EvaluatorOutput(activated=True)
        d2 = EvaluatorOutput(activated=True)
        d3 = EvaluatorOutput(activated=True)

        # 2. 策略: D2 被压制
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow
        wf = OrchestrationWorkflow()
        plan = wf._apply_policies(d1, d2, d3)
        assert plan["d2_suppressed"] is True

        # 3. 执行: E1+E3 并行（D2 被压制，不执行 E2）
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "服务话术"}]},
            ),
            "risk": ExecutorOutput(
                executor_id="risk",
                ui_schema={"action": "PASS"},
                risk_action="PASS",
            ),
            # marketing 不在结果中
        }

        # 4. 仲裁: 无营销 → service_only
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "service_only"

    @pytest.mark.asyncio
    async def test_risk_block_overrides_service(self):
        """风控 BLOCK → 仲裁覆盖服务结果"""
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "服务话术"}]},
            ),
            "risk": ExecutorOutput(
                executor_id="risk",
                ui_schema={"action": "BLOCK", "reason": "违规承诺"},
                risk_action="BLOCK",
            ),
        }
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "risk_blocked"
        assert result.primary_card["type"] == "risk_block"

    @pytest.mark.asyncio
    async def test_risk_warn_degrades_marketing(self):
        """风控 WARN → 营销降级为小卡片"""
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
            "risk": ExecutorOutput(
                executor_id="risk",
                ui_schema={"action": "WARN", "alerts": [{"level": "warning", "message": "注意"}]},
                risk_action="WARN",
            ),
            "marketing": ExecutorOutput(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"name": "产品"}]},
            ),
        }
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "service_risk_warn"
        assert result.marketing_slot is not None
        assert result.marketing_slot["type"] == "marketing_small"
        assert result.risk_badge is not None

    @pytest.mark.asyncio
    async def test_pii_masking_across_full_pipeline(self):
        """全链路 PII 脱敏: 执行器结果 → 仲裁 → 输出"""
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={
                    "scripts": [{"content": "客户张三手机13800138000，卡号6222021234567890"}],
                },
            ),
            "risk": ExecutorOutput(
                executor_id="risk",
                ui_schema={"action": "PASS"},
                risk_action="PASS",
            ),
            "marketing": ExecutorOutput(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"title": "客户李四的专属权益"}]},
            ),
        }
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)

        # primary_card PII 脱敏
        pc_str = str(result.primary_card)
        assert "13800138000" not in pc_str
        assert "6222021234567890" not in pc_str
        assert "[PHONE]" in pc_str
        assert "[BANKCARD]" in pc_str

        # marketing_slot PII 脱敏
        mk_str = str(result.marketing_slot)
        assert "[NAME]" in mk_str


# ══════════════════════════════════════════════════════════════════════
# 5. 状态层 CAS 写回集成
# ══════════════════════════════════════════════════════════════════════


class TestCASWriteBackIntegration:
    """编排→CAS 写回→合并规则 集成"""

    def test_suppress_flow_from_workflow_to_state(self):
        """Workflow suppress → CAS patches → StateManager 合并"""
        from smartcs.services.common.state_manager import StateManager
        from smartcs.workflows.orchestration_workflow import OrchestrationWorkflow

        # 1. Workflow 生成 suppress patches
        wf = OrchestrationWorkflow()
        wf._apply_policies(
            EvaluatorOutput(activated=True),
            EvaluatorOutput(activated=True),
            EvaluatorOutput(activated=True),
        )

        # 2. 构造 CAS patches
        patches: dict[str, Any] = {}
        if wf._suppress_remaining > 0:
            patches["suppress_flag"] = True
        patches["d1_cooldown_remaining"] = 2
        patches["intent_stack"] = ["faq"]

        # 3. StateManager 合并
        mock_redis = AsyncMock()
        sm = StateManager(redis=mock_redis, ttl=1800)
        current = {
            "version": 1,
            "suppress_flag": False,
            "intent_stack": [],
            "d1_cooldown_remaining": 0,
        }
        result = sm._apply_merge_rules(current, patches)
        assert result["suppress_flag"] is True
        assert result["d1_cooldown_remaining"] == 2
        assert result["intent_stack"] == ["faq"]

    def test_suppress_clear_flow(self):
        """suppress 过期 → force_clear → StateManager 允许 true→false"""
        from smartcs.services.common.state_manager import StateManager

        mock_redis = AsyncMock()
        sm = StateManager(redis=mock_redis, ttl=1800)

        # suppress 已过期
        patches = {"suppress_flag": False, "suppress_force_clear": True}
        current = {
            "version": 5,
            "suppress_flag": True,
        }
        result = sm._apply_merge_rules(current, patches)
        assert result["suppress_flag"] is False
        assert "suppress_force_clear" not in result

    def test_cooldown_decrement_flow(self):
        """冷却值递减: D1 激活→冷却2→未激活→递减1→递减0"""
        # 轮1: D1 激活, cooldown_remaining=2
        patches1: dict[str, Any] = {}
        d1_r1 = EvaluatorOutput(activated=True, cooldown_remaining=2)
        if d1_r1.activated:
            patches1["d1_cooldown_remaining"] = 2

        # 轮2: D1 未激活但冷却中, cooldown_remaining=2→1
        patches2: dict[str, Any] = {}
        d1_r2 = EvaluatorOutput(activated=False, cooldown_remaining=2)
        if not d1_r2.activated and d1_r2.cooldown_remaining > 0:
            patches2["d1_cooldown_remaining"] = d1_r2.cooldown_remaining - 1
        assert patches2["d1_cooldown_remaining"] == 1

        # 轮3: D1 未激活, cooldown_remaining=1→0
        patches3: dict[str, Any] = {}
        d1_r3 = EvaluatorOutput(activated=False, cooldown_remaining=1)
        if not d1_r3.activated and d1_r3.cooldown_remaining > 0:
            patches3["d1_cooldown_remaining"] = d1_r3.cooldown_remaining - 1
        assert patches3["d1_cooldown_remaining"] == 0


# ══════════════════════════════════════════════════════════════════════
# 6. DAG + 仲裁 端到端集成
# ══════════════════════════════════════════════════════════════════════


class TestDAGToArbitratorIntegration:
    """DAG 执行结果 → GlobalArbitrator 融合"""

    @pytest.mark.asyncio
    async def test_fast_path_pass_to_arbitrator(self):
        """DAG 快速通路 + 风控 PASS → 仲裁 service_only"""
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG

        # DAG 快速通路
        mock_svc = MagicMock()
        mock_svc.retrieve = MagicMock(return_value=[
            {"script_id": "s1", "content": "话术", "tags": ["faq"], "priority": 5, "score": 0.95}
        ])
        mock_svc.polish = MagicMock(side_effect=lambda s, *a, **kw: s.get("content", "") if isinstance(s, dict) else str(s))
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(return_value=[])

        dag = AIExecutorDAG(script_service=mock_svc, alert_engine=mock_engine)
        dag_result = await dag.run(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.95,
        )

        # DAG 输出交给仲裁器
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema=dag_result.get("ui_schema", {}),
                degraded=dag_result.get("degraded", False),
            ),
            "risk": ExecutorOutput(
                executor_id="risk",
                ui_schema={"action": "PASS"},
                risk_action="PASS",
            ),
        }
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "service_only"

    @pytest.mark.asyncio
    async def test_deep_path_knowledge_to_arbitrator(self):
        """DAG 深度通路 + 风控 WARN → 仲裁 service_risk_warn"""
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG

        mock_svc = MagicMock()
        mock_svc.retrieve = MagicMock(return_value=[])
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(return_value=[])

        dag = AIExecutorDAG(script_service=mock_svc, alert_engine=mock_engine)
        dag_result = await dag.run(
            session_id="s1",
            message="复杂问题",
            intent="faq",
            confidence=0.3,
        )
        assert dag_result["path"] == "deep"

        # 深度通路 + 风控 WARN
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema=dag_result.get("ui_schema", {}),
            ),
            "risk": ExecutorOutput(
                executor_id="risk",
                ui_schema={"action": "WARN", "alerts": [{"level": "warning", "message": "注意"}]},
                risk_action="WARN",
            ),
        }
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "service_risk_warn"
        assert result.risk_badge is not None

    @pytest.mark.asyncio
    async def test_dag_firewall_block_to_arbitrator_risk_block(self):
        """DAG 防火墙拦截 + 风控 BLOCK → 双重拦截"""
        from smartcs.services.assist.ai_executor_dag import AIExecutorDAG

        mock_svc = MagicMock()
        mock_svc.retrieve = MagicMock(return_value=[
            {"script_id": "s1", "content": "保证收益的话术", "tags": ["faq"], "priority": 5, "score": 0.95}
        ])
        mock_svc.polish = MagicMock(side_effect=lambda s, *a, **kw: s.get("content", "") if isinstance(s, dict) else str(s))
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(return_value=[
            {"level": "critical", "category": "compliance", "message": "违规承诺"}
        ])

        dag = AIExecutorDAG(script_service=mock_svc, alert_engine=mock_engine)
        dag_result = await dag.run(
            session_id="s1",
            message="违规内容",
            intent="faq",
            confidence=0.95,
        )
        assert dag_result["firewall_passed"] is False
        assert dag_result["degraded"] is True

        # 仲裁: DAG 已降级 + 风控 BLOCK
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema=dag_result.get("ui_schema", {}),
                degraded=dag_result.get("degraded", False),
            ),
            "risk": ExecutorOutput(
                executor_id="risk",
                ui_schema={"action": "BLOCK", "reason": "合规风险"},
                risk_action="BLOCK",
            ),
        }
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)
        assert result.fusion_type == "risk_blocked"


# ══════════════════════════════════════════════════════════════════════
# 7. Activities 全链路集成
# ══════════════════════════════════════════════════════════════════════


class TestActivitiesIntegration:
    """评估器→执行器→状态写回 Activities 集成"""

    @pytest.mark.asyncio
    async def test_d1_to_e1_with_cooldown(self):
        """D1 激活→E1 执行→冷却值写回"""
        from smartcs.workflows.activities import (
            evaluate_d1_service,
            execute_e1_ai_service,
            reset_breakers,
            reset_dedup_store,
            set_ai_dag,
        )
        reset_breakers()
        reset_dedup_store()

        # D1 评估
        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={"last_confidence": 0.9, "d1_cooldown_remaining": 0},
        )
        d1_result = await evaluate_d1_service(inp)
        assert d1_result.activated is True
        assert d1_result.cooldown_remaining == 2

        # E1 执行
        mock_dag = MagicMock()
        mock_dag.run = AsyncMock(return_value={
            "ui_schema": {"scripts": [{"content": "话术"}]},
            "degraded": False,
            "degradation_type": "",
        })
        set_ai_dag(mock_dag)

        exec_inp = ExecutorInput(
            session_id="s1",
            message="查询",
            intent="faq",
            state_snapshot={"last_confidence": 0.9},
            trace_id="trace-integ-1",
        )
        e1_result = await execute_e1_ai_service(exec_inp)
        assert e1_result.success is True
        assert e1_result.executor_id == "ai_service"

    @pytest.mark.asyncio
    async def test_d2_suppress_blocks_e2(self):
        """D2 被 suppress → E2 不执行"""
        from smartcs.workflows.activities import evaluate_d2_marketing, reset_breakers, reset_dedup_store
        reset_breakers()
        reset_dedup_store()

        inp = EvaluatorInput(
            session_id="s1",
            state_snapshot={
                "last_confidence": 0.9,
                "emotion_vector": {"label": "positive", "score": 0.9},
                "suppress_flag": True,  # D2 被压制
                "d2_cooldown_remaining": 0,
            },
        )
        result = await evaluate_d2_marketing(inp)
        assert result.activated is False
        assert "压制" in result.reason

    @pytest.mark.asyncio
    async def test_e3_critical_to_arbitrator(self):
        """E3 检测到 CRITICAL → 仲裁 BLOCK"""
        from smartcs.workflows.activities import (
            execute_e3_risk,
            reset_breakers,
            reset_dedup_store,
            set_alert_engine_for_risk,
        )
        reset_breakers()
        reset_dedup_store()

        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(return_value=[
            {"level": "critical", "message": "违规承诺", "category": "compliance", "suggestion": "停止"}
        ])
        set_alert_engine_for_risk(mock_engine)

        inp = ExecutorInput(session_id="s1", message="套现包过", trace_id="trace-integ-2")
        e3_result = await execute_e3_risk(inp)
        assert e3_result.risk_action == "BLOCK"

        # 交给仲裁器
        results = {
            "risk": e3_result,
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "话术"}]},
            ),
        }
        arbitrator = GlobalArbitrator()
        arb_result = await arbitrator.arbitrate(results)
        assert arb_result.fusion_type == "risk_blocked"


# ══════════════════════════════════════════════════════════════════════
# 8. 降级全链路集成
# ══════════════════════════════════════════════════════════════════════


class TestDegradationIntegration:
    """多级降级场景集成"""

    @pytest.mark.asyncio
    async def test_e1_degradation_safe_fallback_to_arbitrator(self):
        """E1 降级(safe_fallback) + E3 PASS → 仲裁仍正常输出"""
        e1 = ExecutorOutput(
            executor_id="ai_service",
            degraded=True,
            degradation_type="safe_fallback",
            ui_schema={"fallback": ["安全话术"]},
        )
        e3 = ExecutorOutput(
            executor_id="risk",
            ui_schema={"action": "PASS"},
            risk_action="PASS",
        )
        results = {"ai_service": e1, "risk": e3}
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)
        # 降级结果仍参与仲裁
        assert result.fusion_type == "service_only"

    @pytest.mark.asyncio
    async def test_e3_degradation_pass_with_audit(self):
        """E3 降级(pass_with_audit_flag) → risk_pending_audit=True"""
        e1 = ExecutorOutput(
            executor_id="ai_service",
            ui_schema={"scripts": [{"content": "话术"}]},
        )
        e3 = ExecutorOutput(
            executor_id="risk",
            degraded=True,
            degradation_type="pass_with_audit_flag",
            risk_action="PASS",
            ui_schema={"action": "PASS", "risk_pending_audit": True},
        )
        results = {"ai_service": e1, "risk": e3}
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)
        # 降级但仍放行
        assert result.fusion_type == "service_only"
        # 验证降级结果中有待审标记
        assert e3.ui_schema.get("risk_pending_audit") is True

    @pytest.mark.asyncio
    async def test_all_executors_degraded(self):
        """所有执行器降级 → 仲裁仍能输出"""
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                degraded=True,
                degradation_type="safe_fallback",
                ui_schema={},
            ),
            "marketing": ExecutorOutput(
                executor_id="marketing",
                degraded=True,
                degradation_type="skip_card",
                ui_schema={"marketing_cards": []},
            ),
            "risk": ExecutorOutput(
                executor_id="risk",
                degraded=True,
                degradation_type="pass_with_audit_flag",
                risk_action="PASS",
                ui_schema={"action": "PASS"},
            ),
        }
        arbitrator = GlobalArbitrator()
        result = await arbitrator.arbitrate(results)
        # 即使全部降级，仲裁仍能产出结果
        assert result.fusion_type in ("service_only", "service_marketing")

    @pytest.mark.asyncio
    async def test_temporal_unavailable_falls_back_to_orchestrator(self, client_with_orchestrator):
        """Temporal 不可用时降级到 AssistOrchestrator"""
        client, mock_orch = client_with_orchestrator
        resp = await client.post("/api/analyze", json={
            "session_id": "sess-degrade",
            "message": "查询",
        })
        assert resp.status_code == 200
        mock_orch.process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_orchestrator_returns_empty_payload(self, client: AsyncClient):
        """无 Temporal 且无 Orchestrator → 空推送"""
        resp = await client.post("/api/analyze", json={
            "session_id": "sess-empty",
            "message": "查询",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ══════════════════════════════════════════════════════════════════════
# 9. Session Update 集成
# ══════════════════════════════════════════════════════════════════════


class TestSessionUpdateIntegration:
    """POST /api/session/update 集成"""

    async def test_session_update_without_session_manager(self, client: AsyncClient):
        """无 SessionManager 时返回 5001 错误"""
        resp = await client.post("/api/session/update", json={
            "session_id": "sess-001",
            "phase": "agent",
            "agent_id": "agent-001",
        })
        # 没有 session_manager → 5001 错误
        assert resp.status_code == 500


# ══════════════════════════════════════════════════════════════════════
# 10. 情绪衰减→评估器→仲裁 全链路
# ══════════════════════════════════════════════════════════════════════


class TestEmotionDecayToIntegration:
    """情绪衰减→D2 评估→营销决策 全链路"""

    @pytest.mark.asyncio
    async def test_stale_emotion_prevents_marketing(self):
        """过期情绪 → D2 不激活 → 无营销"""
        from smartcs.workflows.activities import (
            evaluate_d2_marketing,
            reset_breakers,
            reset_dedup_store,
        )
        reset_breakers()
        reset_dedup_store()

        # 10 分钟前的情绪
        old_time = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
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
        # 10 分钟衰减后情绪分数下降，D2 不激活
        assert result.activated is False

    @pytest.mark.asyncio
    async def test_fresh_emotion_allows_marketing(self):
        """新鲜情绪 → D2 激活 → 可执行营销"""
        from smartcs.workflows.activities import (
            evaluate_d2_marketing,
            reset_breakers,
            reset_dedup_store,
        )
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
        assert result.cooldown_remaining == 5
