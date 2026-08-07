"""LLM 调用封装层单元测试"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lumio.services.common.llm import LLMCircuitBreaker, LLMClient
from lumio.shared.config import LLMSettings
from lumio.shared.exceptions import LLMInferenceError

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


# ── chat 重试/空串/超时路径 ──


@pytest.mark.asyncio
async def test_chat_retries_on_empty_content() -> None:
    """空串回复第一次重试, 第二次视为失败"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)

    empty_resp = MagicMock()
    empty_resp.choices = [MagicMock(message=MagicMock(content="  "))]
    good_resp = MagicMock()
    good_resp.choices = [MagicMock(message=MagicMock(content="正常回复"))]

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(side_effect=[empty_resp, good_resp])
        result = await client.chat([{"role": "user", "content": "hi"}])
        assert result == "正常回复"
        assert mock_openai.chat.completions.create.await_count == 2
        assert client.breaker.is_available


@pytest.mark.asyncio
async def test_chat_empty_content_exhausts_retries() -> None:
    """连续空串 → 熔断失败 + LLMInferenceError"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings, breaker=LLMCircuitBreaker(failure_threshold=1))

    empty_resp = MagicMock()
    empty_resp.choices = [MagicMock(message=MagicMock(content=""))]

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(return_value=empty_resp)
        with pytest.raises(LLMInferenceError, match="空内容"):
            await client.chat([{"role": "user", "content": "hi"}])
    assert client.breaker.is_available is False  # 熔断已打开


@pytest.mark.asyncio
async def test_chat_retries_then_timeout_error() -> None:
    """异常重试耗尽 → LLMTimeoutError + 熔断"""
    from lumio.shared.exceptions import LLMTimeoutError

    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings, breaker=LLMCircuitBreaker(failure_threshold=1))

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(LLMTimeoutError):
            await client.chat([{"role": "user", "content": "hi"}])
        assert mock_openai.chat.completions.create.await_count == 2
    assert client.breaker.is_available is False


@pytest.mark.asyncio
async def test_chat_timeout_retry_success() -> None:
    """超时后重试成功"""

    from openai import APITimeoutError

    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)
    good_resp = MagicMock()
    good_resp.choices = [MagicMock(message=MagicMock(content="ok"))]

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(side_effect=[APITimeoutError("slow"), good_resp])
        result = await client.chat([{"role": "user", "content": "hi"}])
        assert result == "ok"


@pytest.mark.asyncio
async def test_chat_passes_timeout_and_json_mode() -> None:
    """timeout 与 json_mode 参数透传"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{"a":1}'))]

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)
        await client.chat([{"role": "user", "content": "hi"}], json_mode=True, timeout=7.5)
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert kwargs["response_format"] == {"type": "json_object"}
        assert kwargs["timeout"] == 7.5


# ── chat_with_tools / _parse_tool_message ──


def _make_tool_response(tool_calls: list[dict] | None = None) -> MagicMock:
    resp = MagicMock()
    msg = MagicMock(content="请稍等")
    if tool_calls:
        tcs = []
        for tc in tool_calls:
            t = MagicMock()
            t.id = tc["id"]
            t.function.name = tc["name"]
            t.function.arguments = tc["arguments"]
            tcs.append(t)
        msg.tool_calls = tcs
    resp.choices = [MagicMock(message=msg)]
    return resp


@pytest.mark.asyncio
async def test_chat_with_tools_text_reply() -> None:
    """无 tool_calls → 纯文本结果"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(return_value=_make_tool_response())
        result = await client.chat_with_tools(
            [{"role": "user", "content": "hi"}], [{"type": "function", "function": {}}]
        )
        assert result.content == "请稍等"
        assert not result.has_tool_calls


@pytest.mark.asyncio
async def test_chat_with_tools_parses_tool_calls() -> None:
    """tool_calls 解析为 ToolCall 列表 + raw_message 保留"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)

    resp = _make_tool_response(
        [
            {"id": "call-1", "name": "query_credit_limit", "arguments": '{"card_type": "platinum"}'},
            {"id": "call-2", "name": "query_points", "arguments": "not-json{"},
        ]
    )
    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(return_value=resp)
        result = await client.chat_with_tools([{"role": "user", "content": "查额度"}], [])
        assert result.has_tool_calls
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].name == "query_credit_limit"
        assert result.tool_calls[0].arguments == {"card_type": "platinum"}
        # 坏 JSON → 空参数
        assert result.tool_calls[1].arguments == {}
        # raw_message 原样保留 arguments
        assert result.raw_message["tool_calls"][0]["function"]["arguments"] == '{"card_type": "platinum"}'


@pytest.mark.asyncio
async def test_chat_with_tools_breaker_open() -> None:
    """熔断打开时 chat_with_tools 抛 LLMInferenceError"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)
    for _ in range(5):
        client.breaker.record_failure()
    with pytest.raises(LLMInferenceError, match="熔断器已打开"):
        await client.chat_with_tools([], [])


@pytest.mark.asyncio
async def test_chat_with_tools_failure_raises() -> None:
    """连续失败 → LLMTimeoutError"""
    from lumio.shared.exceptions import LLMTimeoutError

    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings, breaker=LLMCircuitBreaker(failure_threshold=1))

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(LLMTimeoutError):
            await client.chat_with_tools([], [])
    assert client.breaker.is_available is False


# ── generate / health_check / _record_usage ──


@pytest.mark.asyncio
async def test_generate_assembles_messages() -> None:
    """generate 按 system + history + context + user 组装"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="回答"))]

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await client.generate(
            "你是客服",
            "账单怎么查",
            context="知识片段",
            history=[{"role": "user", "content": "之前的问题"}],
        )
        assert result == "回答"
        messages = mock_openai.chat.completions.create.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "你是客服"}
        assert messages[1] == {"role": "user", "content": "之前的问题"}
        assert "参考知识" in messages[2]["content"]
        assert messages[3] == {"role": "user", "content": "账单怎么查"}


@pytest.mark.asyncio
async def test_health_check_ok() -> None:
    """health_check: 模型列表非空 → True"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)
    with patch.object(client, "_client") as mock_openai:
        mock_openai.models.list = AsyncMock(return_value=MagicMock(data=[MagicMock()]))
        assert await client.health_check() is True


@pytest.mark.asyncio
async def test_health_check_fail() -> None:
    """health_check: 异常 → False"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)
    with patch.object(client, "_client") as mock_openai:
        mock_openai.models.list = AsyncMock(side_effect=RuntimeError("down"))
        assert await client.health_check() is False


@pytest.mark.asyncio
async def test_record_usage_metrics() -> None:
    """_record_usage 触发预算埋点 (响应带 usage)"""
    from lumio.services.common import budget

    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)
    resp = MagicMock()
    resp.usage.prompt_tokens = 100
    resp.usage.completion_tokens = 50
    calls: list[dict] = []

    def fake_record(model, input_tokens, output_tokens, method):
        calls.append({"model": model, "in": input_tokens, "out": output_tokens, "method": method})

    with patch.object(budget, "record_llm_usage", fake_record):
        client._record_usage("m", resp, method="chat")
    assert len(calls) == 1
    assert calls[0]["in"] == 100 and calls[0]["out"] == 50


@pytest.mark.asyncio
async def test_record_usage_no_usage() -> None:
    """响应无 usage → 跳过埋点"""
    settings = LLMSettings(base_url="http://localhost:11434/v1", api_key="test", primary_model="m")
    client = LLMClient(settings=settings)
    resp = MagicMock(usage=None)
    client._record_usage("m", resp, method="chat")  # 不抛异常即可
