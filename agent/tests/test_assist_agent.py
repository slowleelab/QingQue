from __future__ import annotations

import pytest

from smartcs.services.assist.agent import AssistOrchestrator
from smartcs.services.assist.alert_engine import AlertEngine
from smartcs.services.assist.product_catalog import ProductCatalog
from smartcs.services.assist.script_service import ScriptService
from smartcs.shared.models import AssistPushMessage, IntentLabel, SentimentLabel


@pytest.fixture
def orchestrator():
    script_svc = ScriptService()
    script_svc.load_from_memory()
    alert_engine = AlertEngine()
    alert_engine.load_from_memory()
    product_catalog = ProductCatalog()
    return AssistOrchestrator(
        script_service=script_svc,
        alert_engine=alert_engine,
        product_catalog=product_catalog,
        llm_client=None,
        es_client=None,
    )


@pytest.mark.asyncio
async def test_process_message_returns_push_message(orchestrator):
    result = await orchestrator.process(
        session_id="test-001",
        message="我想查一下我的账单",
        intent=IntentLabel.BILL_QUERY,
        sentiment=SentimentLabel.NEUTRAL,
        sentiment_history=[],
        context="客户来电",
    )
    assert isinstance(result, AssistPushMessage)
    assert result.session_id == "test-001"
    assert result.type == "assist_push"


@pytest.mark.asyncio
async def test_process_message_has_scripts(orchestrator):
    result = await orchestrator.process(
        session_id="test-002",
        message="分期怎么办理",
        intent=IntentLabel.INSTALLMENT_INQUIRY,
        sentiment=SentimentLabel.NEUTRAL,
        sentiment_history=[],
        context="客户咨询分期业务",
    )
    assert len(result.payload.scripts) > 0


@pytest.mark.asyncio
async def test_process_message_triggers_compliance_alert(orchestrator):
    result = await orchestrator.process(
        session_id="test-003",
        message="我可以帮你套现，包过",
        intent=IntentLabel.FAQ,
        sentiment=SentimentLabel.NEUTRAL,
        sentiment_history=[],
        context="测试",
    )
    assert len(result.payload.alerts) > 0


def test_should_throttle_first_message(orchestrator):
    assert orchestrator.should_throttle("new-session") is False


def test_should_throttle_repeated(orchestrator):
    """节流已统一到 PushTracker（Redis 持久化），should_throttle 始终返回 False"""
    orchestrator.should_throttle("s1")  # first
    assert orchestrator.should_throttle("s1") is False  # 节流由 PushTracker 管理
