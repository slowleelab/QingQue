"""LLM 调用封装层单元测试"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smartcs.services.common.llm import LLMCircuitBreaker, LLMClient
from smartcs.shared.config import LLMSettings
from smartcs.shared.exceptions import LLMInferenceError

# ── LLMCircuitBreaker ──


def test_breaker_initial_state() -> None:
    """熔断器初始状态应为闭合（可用）"""
    breaker = LLMCircuitBreaker()
    assert breaker.is_available is True


def test_breaker_opens_after_threshold() -> None:
    """连续失败达到阈值后应打开熔断"""
    breaker = LLMCircuitBreaker(failure_threshold=3)
    for _ in range(3):
        breaker.record_failure()
    assert breaker.is_available is False


def test_breaker_closes_after_success() -> None:
    """成功调用后应关闭熔断"""
    breaker = LLMCircuitBreaker(failure_threshold=3)
    for _ in range(3):
        breaker.record_failure()
    assert breaker.is_available is False

    breaker.record_success()
    assert breaker.is_available is True


def test_breaker_half_open_after_recovery_timeout() -> None:
    """冷却期后应进入半开状态"""
    import time

    breaker = LLMCircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_available is False

    time.sleep(0.15)
    assert breaker.is_available is True


# ── LLMClient ──


@pytest.mark.asyncio
async def test_chat_success() -> None:
    """chat() 正常调用应返回模型输出"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="test-model")
    client = LLMClient(settings=settings)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="你好，有什么可以帮您？"))]

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await client.chat([{"role": "user", "content": "你好"}])
        assert result == "你好，有什么可以帮您？"


@pytest.mark.asyncio
async def test_chat_json_parses_output() -> None:
    """chat_json() 应解析 JSON 输出"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="test-model")
    client = LLMClient(settings=settings)

    json_output = json.dumps({"intent": "bill_query", "confidence": 0.9})
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=json_output))]

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await client.chat_json([{"role": "user", "content": "查询账单"}])
        assert result["intent"] == "bill_query"
        assert result["confidence"] == 0.9


@pytest.mark.asyncio
async def test_chat_json_raises_on_invalid_json() -> None:
    """chat_json() 在 JSON 解析失败时应抛出 LLMInferenceError"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="test-model")
    client = LLMClient(settings=settings)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="not json"))]

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)
        with pytest.raises(LLMInferenceError, match="JSON 解析失败"):
            await client.chat_json([{"role": "user", "content": "test"}])


@pytest.mark.asyncio
async def test_chat_raises_when_breaker_open() -> None:
    """熔断器打开时应抛出 LLMInferenceError"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="test-model")
    client = LLMClient(settings=settings)
    # 手动打开熔断器
    for _ in range(5):
        client.breaker.record_failure()

    with pytest.raises(LLMInferenceError, match="熔断器已打开"):
        await client.chat([{"role": "user", "content": "test"}])


@pytest.mark.asyncio
async def test_classify_uses_fallback_model() -> None:
    """classify() 应使用 fallback_model"""
    settings = LLMSettings(
        base_url="http://localhost:11434/v1",
        api_key="test",
        primary_model="qwen2.5:14b",
        fallback_model="qwen2.5:7b",
    )
    client = LLMClient(settings=settings)

    json_output = json.dumps({"intent": "chitchat", "confidence": 0.8, "entities": [], "sentiment": "neutral"})
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=json_output))]

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await client.classify("system prompt", "你好")
        assert result["intent"] == "chitchat"
        # 验证使用了 fallback_model
        call_kwargs = mock_openai.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("model") == "qwen2.5:7b"
