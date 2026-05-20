"""降级策略单元测试"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.common.degradation import (
    ContentDegrader,
    DegradationManager,
    HealthMonitor,
)
from smartcs.shared.models import DegradationLevel, IntentLabel

# ── DegradationLevel ──


def test_degradation_level_values():
    assert DegradationLevel.NORMAL.value == "normal"
    assert DegradationLevel.DEGRADED.value == "degraded"
    assert DegradationLevel.FALLBACK.value == "fallback"
    levels = list(DegradationLevel)
    assert levels == [DegradationLevel.NORMAL, DegradationLevel.DEGRADED, DegradationLevel.FALLBACK]


# ── HealthMonitor state machine ──


class MockLLMClient:
    def __init__(self, fail_count: int = 0):
        self.call_count = 0
        self.fail_count = fail_count

    async def chat(self, messages, **kwargs):
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise Exception("模拟 LLM 不可用")
        return "pong"


class MockBreaker:
    def __init__(self, is_available: bool = True):
        self._available = is_available

    @property
    def is_available(self) -> bool:
        return self._available


@pytest.mark.asyncio
async def test_health_monitor_initial_state():
    llm = MockLLMClient()
    breaker = MockBreaker()
    monitor = HealthMonitor(llm_client=llm, breaker=breaker, probe_interval=0.1, probe_timeout=5.0, fail_threshold=2, success_threshold=2)
    assert monitor.level == DegradationLevel.NORMAL
    assert monitor.is_llm_available is True


@pytest.mark.asyncio
async def test_health_monitor_probe_success():
    llm = MockLLMClient()
    breaker = MockBreaker()
    monitor = HealthMonitor(llm_client=llm, breaker=breaker, probe_interval=0.1, probe_timeout=5.0, fail_threshold=2, success_threshold=2)
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.NORMAL


@pytest.mark.asyncio
async def test_health_monitor_degraded_after_failures():
    llm = MockLLMClient(fail_count=10)
    breaker = MockBreaker()
    monitor = HealthMonitor(llm_client=llm, breaker=breaker, probe_interval=0.1, probe_timeout=5.0, fail_threshold=2, success_threshold=2)
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.NORMAL
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.DEGRADED


@pytest.mark.asyncio
async def test_health_monitor_fallback_when_breaker_open():
    llm = MockLLMClient()
    breaker = MockBreaker(is_available=False)
    monitor = HealthMonitor(llm_client=llm, breaker=breaker, probe_interval=0.1, probe_timeout=5.0, fail_threshold=2, success_threshold=2)
    assert monitor.level == DegradationLevel.FALLBACK


@pytest.mark.asyncio
async def test_health_monitor_recovery():
    llm = MockLLMClient(fail_count=2)
    breaker = MockBreaker()
    monitor = HealthMonitor(llm_client=llm, breaker=breaker, probe_interval=0.1, probe_timeout=5.0, fail_threshold=2, success_threshold=2)
    await monitor._probe_once()
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.DEGRADED
    # recover
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.DEGRADED
    await monitor._probe_once()
    assert monitor.level == DegradationLevel.NORMAL


@pytest.mark.asyncio
async def test_health_monitor_backoff():
    monitor = HealthMonitor(llm_client=MockLLMClient(), breaker=MockBreaker(), probe_interval=1.0, probe_timeout=5.0, fail_threshold=2, success_threshold=2)
    assert monitor._current_interval == 1.0
    monitor._consecutive_failures = 3
    monitor._update_interval()
    assert monitor._current_interval == 8.0  # min(2^3, 30)
    monitor._consecutive_failures = 10
    monitor._update_interval()
    assert monitor._current_interval == 30.0  # capped


# ── ContentDegrader ──


def test_retrieval_summary_with_context():
    degrader = ContentDegrader()
    context = "账单查询方法：登录APP查看账单详情。\n\n还款方式：支持自动还款和手动还款。"
    result = degrader.retrieval_summary(context)
    assert "账单查询方法" in result
    assert "还有" in result


def test_retrieval_summary_empty():
    degrader = ContentDegrader()
    assert degrader.retrieval_summary("") == ""


def test_retrieval_summary_single_chunk():
    degrader = ContentDegrader()
    result = degrader.retrieval_summary("信用卡额度调整规则：根据用卡情况每半年评估一次。")
    assert "信用卡额度调整规则" in result
    assert "还有" not in result


def test_get_template_knowledge_intents():
    degrader = ContentDegrader()
    assert degrader.get_template(IntentLabel.BILL_QUERY) != degrader.get_template(IntentLabel.REWARD_QUERY)
    assert "账单" in degrader.get_template(IntentLabel.BILL_QUERY)


def test_get_template_business_intents():
    degrader = ContentDegrader()
    assert "转人工" in degrader.get_template(IntentLabel.CARD_LOSS)
    assert "转人工" in degrader.get_template(IntentLabel.COMPLAINT)


def test_get_template_fallback():
    degrader = ContentDegrader()
    template = degrader.get_template(IntentLabel.CHITCHAT)
    assert "抱歉" in template or "问题" in template


def test_hardcoded_fallback():
    degrader = ContentDegrader()
    result = degrader.hardcoded_fallback()
    assert "抱歉" in result or "服务" in result
    assert len(result) > 0


# ── DegradationManager ──


@pytest.fixture
def mock_llm_client_fixture():
    client = MagicMock()
    client.breaker = MagicMock()
    client.breaker.is_available = True
    return client


@pytest.fixture
def mock_health_monitor_fixture():
    monitor = MagicMock()
    monitor.level = DegradationLevel.NORMAL
    monitor.is_llm_available = True
    return monitor


@pytest.fixture
def mock_content_degrader_fixture():
    degrader = MagicMock(spec=ContentDegrader)
    degrader.retrieval_summary.return_value = "检索摘要"
    degrader.get_template.return_value = "模板回复"
    degrader.hardcoded_fallback.return_value = "服务不可用"
    return degrader


@pytest.mark.asyncio
async def test_generate_normal_success(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture):
    mock_llm_client_fixture.generate = AsyncMock(return_value="LLM 生成的回复")
    mgr = DegradationManager(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture)
    result = await mgr.generate_with_fallback(system_prompt="你是客服", user_input="账单怎么查", context="账单查询：登录APP...")
    assert result.content == "LLM 生成的回复"
    assert result.source == "llm"


@pytest.mark.asyncio
async def test_generate_normal_llm_timeout_falls_back_to_retrieval(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture):
    mock_llm_client_fixture.generate = AsyncMock(side_effect=Exception("超时"))
    mgr = DegradationManager(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture)
    result = await mgr.generate_with_fallback(system_prompt="你是客服", user_input="账单怎么查", context="账单查询相关信息...")
    assert result.source == "retrieval"
    mock_content_degrader_fixture.retrieval_summary.assert_called_once()


@pytest.mark.asyncio
async def test_generate_normal_no_context_falls_back_to_template(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture):
    mock_llm_client_fixture.generate = AsyncMock(side_effect=Exception("超时"))
    mgr = DegradationManager(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture)
    result = await mgr.generate_with_fallback(system_prompt="你是客服", user_input="你好", context="", intent_label=IntentLabel.CHITCHAT)
    assert result.source == "template"
    mock_content_degrader_fixture.get_template.assert_called_once_with(IntentLabel.CHITCHAT)


@pytest.mark.asyncio
async def test_generate_degraded_skips_llm(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture):
    mock_health_monitor_fixture.level = DegradationLevel.DEGRADED
    mock_llm_client_fixture.generate = AsyncMock()
    mgr = DegradationManager(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture)
    result = await mgr.generate_with_fallback(system_prompt="你是客服", user_input="账单怎么查", context="账单查询相关信息...")
    assert result.source == "retrieval"
    mock_llm_client_fixture.generate.assert_not_called()


@pytest.mark.asyncio
async def test_generate_fallback_skips_llm_and_retrieval(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture):
    mock_health_monitor_fixture.level = DegradationLevel.FALLBACK
    mock_llm_client_fixture.generate = AsyncMock()
    mgr = DegradationManager(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture)
    result = await mgr.generate_with_fallback(system_prompt="你是客服", user_input="账单怎么查", context="账单查询相关信息...", intent_label=IntentLabel.BILL_QUERY)
    assert result.source == "template"
    mock_llm_client_fixture.generate.assert_not_called()
    mock_content_degrader_fixture.get_template.assert_called_once_with(IntentLabel.BILL_QUERY)


@pytest.mark.asyncio
async def test_degradation_manager_level_property(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture):
    mgr = DegradationManager(mock_llm_client_fixture, mock_health_monitor_fixture, mock_content_degrader_fixture)
    assert mgr.level == DegradationLevel.NORMAL
