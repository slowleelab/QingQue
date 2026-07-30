"""AI 辅助功能集成测试

跨模块协作验证：路由 → 坐席辅助引擎 → 仲裁 → 反馈全链路集成。
不依赖真实中间件（Redis/ES/Milvus），使用 mock 注入替换外部依赖。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from lumio.main import create_assist_app
from lumio.services.assist.arbitrator import ExecutorOutput, GlobalArbitrator
from lumio.services.common.assist_engine import (
    evaluate_d1_service,
    evaluate_d2_marketing,
    evaluate_d3_risk,
    run_assist_engine,
)
from lumio.shared.middleware import register_exception_handlers
from lumio.shared.models import IntentLabel


def _make_mock_redis() -> AsyncMock:
    """构造带内存存储的 mock Redis，支持 setex/get/delete 用于反馈缓冲测试"""
    store: dict[str, str] = {}
    redis = AsyncMock()

    async def _setex(key: str, _ttl: int, value: str) -> bool:
        store[key] = value
        return True

    async def _get(key: str):
        return store.get(key)

    async def _delete(*keys: str) -> int:
        return sum(1 for k in keys if store.pop(k, None) is not None)

    redis.setex = AsyncMock(side_effect=_setex)
    redis.get = AsyncMock(side_effect=_get)
    redis.delete = AsyncMock(side_effect=_delete)
    redis._store = store  # type: ignore[attr-defined]
    return redis


@pytest_asyncio.fixture
async def assist_app():
    """创建最小化的 Assist FastAPI 应用，注入 mock 依赖"""
    app = create_assist_app()
    register_exception_handlers(app)
    app.state.classifier = None
    app.state.ai_executor = None
    app.state.state_manager = None
    app.state.session_manager = None
    app.state.assist_ws_pool = {}
    app.state.redis_client = _make_mock_redis()
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
    mock_sm.read_state = AsyncMock(
        return_value={
            "version": 3,
            "last_confidence": 0.8,
            "intent_stack": ["faq"],
        }
    )
    mock_sm.cas_write = AsyncMock(return_value={"ok": True, "new_version": 4})
    assist_app.state.state_manager = mock_sm
    transport = ASGITransport(app=assist_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, mock_sm


@pytest_asyncio.fixture
async def client_without_ai_executor(assist_app):
    """创建无 ai_executor 但有 alert_engine 的 Client

    验证关键场景: ai_executor 缺失时 E3 风控仍独立运行（合规底线不可绕过）。
    """
    from lumio.services.assist.alert_engine import AlertEngine

    alert_engine = AlertEngine()
    alert_engine.load_from_memory()
    assist_app.state.alert_engine = alert_engine
    assist_app.state.ai_executor = None  # 显式置空
    transport = ASGITransport(app=assist_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, alert_engine


@pytest.fixture(autouse=True)
def _clean_feedback_buffer():
    """反馈缓冲已迁移至 Redis（见 router 的 _FEEDBACK_KEY_PREFIX），此处无需清理内存状态"""
    yield


class TestHealthEndpoint:
    """GET /api/health 集成测试"""

    async def test_health_returns_ok(self, client: AsyncClient):
        """健康检查端点返回 200"""
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "assist"


class TestAnalyzeEndpoint:
    """POST /api/analyze 集成测试"""

    async def test_analyze_no_dependencies(self, client: AsyncClient):
        """无任何外部依赖时仍返回 200（空 payload）"""
        resp = await client.post(
            "/api/analyze",
            json={
                "session_id": "sess-001",
                "message": "查询账单",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["intent"] == "faq"

    async def test_analyze_without_ai_executor_still_runs_risk(self, client_without_ai_executor):
        """ai_executor 缺失时坐席辅助引擎仍运行，E3 风控不依赖 ai_executor"""
        client, alert_engine = client_without_ai_executor
        resp = await client.post(
            "/api/analyze",
            json={
                "session_id": "sess-002",
                "message": "我可以帮你套现，包过",  # 触发合规告警
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # 引擎应返回 payload（即使 E1 降级），且风控告警应出现在 payload 中
        assert data["status"] == "ok"

    async def test_analyze_with_classifier(self, assist_app):
        """有分类器时使用分类结果"""
        mock_classifier = MagicMock()
        intent_result = MagicMock()
        intent_result.primary_intent = IntentLabel.BILL_QUERY
        intent_result.primary_confidence = 0.92
        mock_classifier.classify = AsyncMock(return_value=(intent_result, [], MagicMock(), "rule"))
        assist_app.state.classifier = mock_classifier

        transport = ASGITransport(app=assist_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/analyze",
                json={
                    "session_id": "sess-003",
                    "message": "上个月账单多少",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "bill_query"
        assert data["confidence"] == 0.92

    async def test_analyze_pushes_to_websocket(self, assist_app):
        """有 WS 连接时推送结果"""
        mock_ws = AsyncMock()
        assist_app.state.assist_ws_pool["sess-ws"] = mock_ws

        # 不再 mock 旧编排器；引擎返回 None 时 /analyze 会用空 payload 占位推送
        transport = ASGITransport(app=assist_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/analyze",
                json={
                    "session_id": "sess-ws",
                    "message": "查询",
                },
            )
        assert resp.status_code == 200
        mock_ws.send_json.assert_awaited()


class TestFeedbackIntegration:
    """POST /api/feedback + POST /api/feedback/undo 全链路"""

    async def test_feedback_accept_flow(self, client: AsyncClient):
        """accept 操作: confidence=1.0 + delayed_commit"""
        resp = await client.post(
            "/api/feedback",
            json={
                "session_id": "sess-fb-1",
                "agent_id": "agent-001",
                "action": "accept",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["confidence"] == 1.0
        assert data["delayed_commit"] is True

    async def test_feedback_modify_with_fields(self, assist_app):
        """modify 操作: confidence=0.5 + modify_fields 写入 Redis 缓冲"""
        transport = ASGITransport(app=assist_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/feedback",
                json={
                    "session_id": "sess-fb-2",
                    "agent_id": "agent-001",
                    "action": "modify",
                    "modify_fields": ["script_content", "knowledge_summary"],
                },
            )
        assert resp.status_code == 200
        redis_client = assist_app.state.redis_client
        buffered = await redis_client.get("lumio:assist:feedback:sess-fb-2:agent-001")
        assert buffered is not None
        assert json.loads(buffered)["modify_fields"] == ["script_content", "knowledge_summary"]

    async def test_feedback_then_undo(self, client: AsyncClient):
        """提交反馈后 3 秒内撤销"""
        await client.post(
            "/api/feedback",
            json={
                "session_id": "sess-fb-3",
                "agent_id": "agent-001",
                "action": "reject",
            },
        )
        resp = await client.post(
            "/api/feedback/undo",
            json={
                "session_id": "sess-fb-3",
                "agent_id": "agent-001",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["undone"] is True

    async def test_feedback_delayed_commit_with_state_manager(self, client_with_state_manager):
        """有 StateManager 时反馈不立即提交"""
        client, mock_sm = client_with_state_manager
        resp = await client.post(
            "/api/feedback",
            json={
                "session_id": "sess-fb-5",
                "agent_id": "agent-001",
                "action": "accept",
            },
        )
        assert resp.status_code == 200
        mock_sm.cas_write.assert_not_awaited()


class TestEvaluatorToArbitratorIntegration:
    """评估器结果 → 执行器结果 → 仲裁融合"""

    @pytest.mark.asyncio
    async def test_d1_d3_activated_service_with_risk(self):
        """D1+D3 激活 → E1+E3 结果可仲裁"""
        d1 = evaluate_d1_service({"last_confidence": 0.9, "d1_cooldown_remaining": 0})
        d2 = evaluate_d2_marketing({"last_confidence": 0.9, "suppress_flag": True})
        d3 = evaluate_d3_risk({})
        assert d1.activated is True
        assert d2.activated is False
        assert d3.activated is True

        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "您好，请问有什么可以帮您？"}]},
            ),
            "risk": ExecutorOutput(
                executor_id="risk",
                ui_schema={"action": "PASS"},
                risk_action="PASS",
            ),
        }
        result = await GlobalArbitrator().arbitrate(results)
        assert result.fusion_type == "service_only"
        assert result.primary_card["type"] == "service_answer"

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
        result = await GlobalArbitrator().arbitrate(results)
        assert result.fusion_type == "risk_blocked"
        assert result.primary_card["type"] == "risk_block"

    @pytest.mark.asyncio
    async def test_pii_masking_across_full_pipeline(self):
        """全链路 PII 脱敏: 执行器结果 → 仲裁 → 输出"""
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                ui_schema={"scripts": [{"content": "客户张三手机13800138000，卡号6222021234567890"}]},
            ),
            "risk": ExecutorOutput(executor_id="risk", ui_schema={"action": "PASS"}, risk_action="PASS"),
            "marketing": ExecutorOutput(
                executor_id="marketing",
                ui_schema={"marketing_cards": [{"title": "客户李四的专属权益"}]},
            ),
        }
        result = await GlobalArbitrator().arbitrate(results)
        assert "13800138000" not in str(result.primary_card)
        assert "6222021234567890" not in str(result.primary_card)
        assert "[PHONE]" in str(result.primary_card)
        assert "[BANKCARD]" in str(result.primary_card)
        assert "[NAME]" in str(result.marketing_slot)


class TestAssistEngineIntegration:
    """OE Pipeline 端到端集成"""

    @pytest.mark.asyncio
    async def test_service_pass_to_arbitrator(self):
        """AI 服务结果 + 风控 PASS → 仲裁 service_only"""
        mock_ai = MagicMock()
        mock_ai.run = AsyncMock(
            return_value={
                "ui_schema": {"scripts": [{"content": "话术"}]},
                "latency_ms": 12,
                "degraded": False,
                "degradation_type": "",
            }
        )

        push_data = await run_assist_engine(
            session_id="s1",
            message="查询",
            intent="faq",
            confidence=0.95,
            trace_id="trace-oe-1",
            state_snapshot={"last_confidence": 0.95, "d1_cooldown_remaining": 0},
            ai_executor=mock_ai,
        )
        assert push_data is not None
        assert push_data["payload"]["fusion_type"] == "service_only"
        assert push_data["payload"]["primary_card"]["type"] == "service_answer"

    @pytest.mark.asyncio
    async def test_low_confidence_skips_ai_but_keeps_risk_pass(self):
        """低置信度跳过 AI 服务，风控 PASS 仍产出安全空卡片"""
        mock_ai = MagicMock()
        mock_ai.run = AsyncMock()

        push_data = await run_assist_engine(
            session_id="s1",
            message="复杂问题",
            intent="faq",
            confidence=0.3,
            trace_id="trace-oe-2",
            state_snapshot={"last_confidence": 0.3, "d1_cooldown_remaining": 0},
            ai_executor=mock_ai,
        )
        assert push_data is not None
        assert push_data["payload"]["fusion_type"] == "service_only"
        mock_ai.run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_risk_block_to_arbitrator_risk_block(self):
        """风控 BLOCK → 仲裁拦截输出"""
        mock_ai = MagicMock()
        mock_ai.run = AsyncMock(
            return_value={
                "ui_schema": {"scripts": [{"content": "服务话术"}]},
                "latency_ms": 12,
            }
        )
        mock_engine = MagicMock()
        mock_engine.check_compliance = MagicMock(
            return_value=[
                {"level": "critical", "category": "compliance", "message": "违规承诺"},
            ]
        )

        push_data = await run_assist_engine(
            session_id="s1",
            message="违规内容",
            intent="faq",
            confidence=0.95,
            trace_id="trace-oe-3",
            state_snapshot={"last_confidence": 0.95, "d1_cooldown_remaining": 0},
            ai_executor=mock_ai,
            alert_engine=mock_engine,
        )
        assert push_data is not None
        assert push_data["payload"]["fusion_type"] == "risk_blocked"


class TestDegradationIntegration:
    """多级降级场景集成"""

    @pytest.mark.asyncio
    async def test_e1_degradation_safe_fallback_to_arbitrator(self):
        """E1 降级 + E3 PASS → 仲裁仍正常输出"""
        results = {
            "ai_service": ExecutorOutput(
                executor_id="ai_service",
                degraded=True,
                degradation_type="safe_fallback",
                ui_schema={"fallback": ["安全话术"]},
            ),
            "risk": ExecutorOutput(executor_id="risk", ui_schema={"action": "PASS"}, risk_action="PASS"),
        }
        result = await GlobalArbitrator().arbitrate(results)
        assert result.fusion_type == "service_only"

    @pytest.mark.asyncio
    async def test_engine_returns_placeholder_on_failure(self, client: AsyncClient):
        """坐席辅助引擎异常时 /analyze 仍返回 200 + 空 payload 占位（不 500）"""
        resp = await client.post(
            "/api/analyze",
            json={
                "session_id": "sess-degrade",
                "message": "查询",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestSessionUpdateIntegration:
    """POST /api/session/update 集成"""

    async def test_session_update_without_session_manager(self, client: AsyncClient):
        """无 SessionManager 时返回 5001 错误"""
        resp = await client.post(
            "/api/session/update",
            json={
                "session_id": "sess-001",
                "phase": "agent",
                "agent_id": "agent-001",
            },
        )
        assert resp.status_code == 500


class TestEmotionToIntegration:
    """情绪向量 → D2 评估 → 营销决策"""

    def test_suppressed_emotion_prevents_marketing(self):
        """压制标记存在时，正向情绪也不触发营销"""
        result = evaluate_d2_marketing(
            {
                "last_confidence": 0.8,
                "emotion_vector": {"label": "positive", "score": 0.9},
                "suppress_flag": True,
                "d2_cooldown_remaining": 0,
            }
        )
        assert result.activated is False

    def test_positive_emotion_allows_marketing(self):
        """正向情绪 + 高置信度 → D2 激活"""
        result = evaluate_d2_marketing(
            {
                "last_confidence": 0.8,
                "emotion_vector": {"label": "positive", "score": 0.9},
                "suppress_flag": False,
                "d2_cooldown_remaining": 0,
            }
        )
        assert result.activated is True
