"""大模型调用封装层

支持 OpenAI 兼容 API（Ollama / vLLM），提供：
- 结构化输出（json_mode）
- 超时 + 重试 + 指数退避
- 熔断器保护
- 降级链：LLM 生成 → 检索摘要 → 模板回复 → 兜底
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

# P1-3: openai.APITimeoutError 不继承内置 TimeoutError, 统一引用避免死分支
try:
    from openai import APITimeoutError as _APITimeoutError
except ImportError:  # 旧版 SDK
    _APITimeoutError = TimeoutError  # type: ignore[misc]

from lumio.shared.config import LLMSettings, get_settings
from lumio.shared.exceptions import LLMInferenceError, LLMTimeoutError
from lumio.shared.metrics import LLM_CALL_DURATION
from lumio.shared.tracing import traced

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """LLM 请求的单次工具调用"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolCallResult:
    """``chat_with_tools`` 返回结构

    区分两种终态：
    - ``tool_calls`` 非空 → 模型请求调用工具（需执行后回喂）
    - ``tool_calls`` 为空 → 模型给出最终文本答复（``content``）
    """

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # 原始 assistant message（含 tool_calls），回喂 LLM 时需原样带上
    raw_message: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMCircuitBreaker:
    """LLM 熔断器

    连续失败达到阈值后打开熔断（标记不可用），
    冷却期后进入半开状态允许一次试探，成功则关闭熔断。
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._consecutive_failures = 0
        self._is_open = False
        self._last_failure_time: float = 0.0

    @property
    def is_available(self) -> bool:
        """熔断器是否闭合（服务可用）"""
        if not self._is_open:
            return True
        # 冷却期后进入半开状态
        return time.monotonic() - self._last_failure_time >= self._recovery_timeout

    def record_success(self) -> None:
        """记录成功调用"""
        self._consecutive_failures = 0
        self._is_open = False

    def record_failure(self) -> None:
        """记录失败调用"""
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()
        if self._consecutive_failures >= self._failure_threshold:
            self._is_open = True
            logger.warning("LLM 熔断器打开：连续 %d 次调用失败", self._consecutive_failures)


class LLMClient:
    """大模型调用客户端

    封装 OpenAI 兼容 API 调用，支持结构化输出、重试、熔断和降级。
    """

    def __init__(
        self,
        settings: LLMSettings | None = None,
        breaker: LLMCircuitBreaker | None = None,
    ) -> None:
        self._settings = settings or get_settings().llm
        self._breaker = breaker or LLMCircuitBreaker()
        self._client = AsyncOpenAI(
            base_url=self._settings.base_url,
            api_key=self._settings.api_key,
            timeout=self._settings.timeout_seconds,
        )

    @property
    def breaker(self) -> LLMCircuitBreaker:
        """熔断器实例"""
        return self._breaker

    @traced("Agent: llm_generate")
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        timeout: float | None = None,
    ) -> str:
        """调用 ChatCompletion 接口

        Args:
            messages: 消息列表 [{"role": "system"|"user"|"assistant", "content": "..."}]
            model: 模型名称，None 时使用配置中的 primary_model
            temperature: 采样温度
            max_tokens: 最大生成 token 数
            json_mode: 是否启用 JSON 结构化输出

        Returns:
            模型生成的文本内容

        Raises:
            LLMTimeoutError: 调用超时
            LLMInferenceError: 调用失败或熔断器打开
        """
        if not self._breaker.is_available:
            raise LLMInferenceError("LLM 熔断器已打开，服务暂时不可用")

        kwargs: dict[str, Any] = {
            "model": model or self._settings.primary_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self._settings.temperature,
            "max_tokens": max_tokens or self._settings.max_tokens,
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        if timeout is not None:
            kwargs["timeout"] = timeout  # OpenAI SDK per-request timeout override

        _start = time.monotonic()
        last_error: Exception | None = None
        max_retries = 2  # 1 次初始 + 1 次重试

        for attempt in range(max_retries):
            try:
                response = await self._client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content or ""
                self._breaker.record_success()
                elapsed = time.monotonic() - _start
                LLM_CALL_DURATION.labels(model=kwargs["model"], method="chat").observe(elapsed)
                logger.debug(
                    "LLM call succeeded: model=%s, latency_ms=%d, tokens=%d",
                    kwargs["model"],
                    int(elapsed * 1000),
                    response.usage.total_tokens if response.usage else 0,
                )
                self._record_usage(kwargs["model"], response, method="chat")  # P0-6
                return content
            except (TimeoutError, _APITimeoutError) as exc:
                # P1-3 第三轮修复: openai.APITimeoutError 的 MRO 不继承内置 TimeoutError,
                # 旧 except TimeoutError 是死分支 (SDK 超时落入泛化 except, 超时统计永远不触发)
                last_error = exc
                logger.warning("LLM 调用超时 (attempt %d/%d)", attempt + 1, max_retries)
            except Exception as exc:
                last_error = exc
                logger.warning("LLM 调用异常 (attempt %d/%d): %s", attempt + 1, max_retries, exc)

            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (2**attempt))

        self._breaker.record_failure()
        raise LLMTimeoutError(f"LLM 调用失败: {last_error}") from last_error

    @traced("Agent: llm_tool_calling")
    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> ToolCallResult:
        """调用支持 function-calling 的 ChatCompletion 接口

        与 ``chat()`` 并存、互不影响。传入 OpenAI 格式的 ``tools``，
        由模型自主决定是否调用工具（``tool_choice="auto"``）。

        Args:
            messages: 消息列表（可含 role=tool 的工具返回消息）
            tools: OpenAI 格式工具列表 [{"type": "function", "function": {...}}]
            model: 模型名称，None 时使用 primary_model
            temperature: 采样温度
            max_tokens: 最大生成 token 数
            timeout: 单次调用超时时间（秒）

        Returns:
            ToolCallResult：含最终文本或待执行的 tool_calls

        Raises:
            LLMTimeoutError: 调用超时或失败
            LLMInferenceError: 熔断器打开
        """
        if not self._breaker.is_available:
            raise LLMInferenceError("LLM 熔断器已打开，服务暂时不可用")

        kwargs: dict[str, Any] = {
            "model": model or self._settings.primary_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self._settings.temperature,
            "max_tokens": max_tokens or self._settings.max_tokens,
            "tools": tools,
            "tool_choice": "auto",
        }
        if timeout is not None:
            kwargs["timeout"] = timeout

        _start = time.monotonic()
        last_error: Exception | None = None
        max_retries = 2  # 1 次初始 + 1 次重试

        for attempt in range(max_retries):
            try:
                response = await self._client.chat.completions.create(**kwargs)
                message = response.choices[0].message
                self._breaker.record_success()
                elapsed = time.monotonic() - _start
                LLM_CALL_DURATION.labels(model=kwargs["model"], method="chat_with_tools").observe(elapsed)
                result = self._parse_tool_message(message)
                logger.debug(
                    "LLM tool-calling succeeded: model=%s, latency_ms=%d, tool_calls=%d",
                    kwargs["model"],
                    int(elapsed * 1000),
                    len(result.tool_calls),
                )
                self._record_usage(kwargs["model"], response, method="chat_with_tools")  # P0-6
                return result
            except (TimeoutError, _APITimeoutError) as exc:
                last_error = exc
                logger.warning("LLM tool-calling 超时 (attempt %d/%d)", attempt + 1, max_retries)
            except Exception as exc:
                last_error = exc
                logger.warning("LLM tool-calling 异常 (attempt %d/%d): %s", attempt + 1, max_retries, exc)

            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (2**attempt))

        self._breaker.record_failure()
        raise LLMTimeoutError(f"LLM tool-calling 调用失败: {last_error}") from last_error

    @staticmethod
    def _parse_tool_message(message: Any) -> ToolCallResult:
        """解析 ChatCompletion message → ToolCallResult"""
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        tool_calls: list[ToolCall] = []
        raw_message: dict[str, Any] = {"role": "assistant", "content": message.content or ""}

        if raw_tool_calls:
            raw_message["tool_calls"] = []
            for tc in raw_tool_calls:
                raw_args = tc.function.arguments or "{}"
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    logger.warning("工具参数 JSON 解析失败，按空参数处理: %s", raw_args)
                    parsed_args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=parsed_args))
                raw_message["tool_calls"].append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": raw_args},
                    }
                )

        return ToolCallResult(content=message.content or "", tool_calls=tool_calls, raw_message=raw_message)

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """调用 ChatCompletion 并解析 JSON 输出

        启用 json_mode，自动解析返回的 JSON 字符串。

        Args:
            messages: 消息列表
            model: 模型名称
            temperature: 采样温度
            max_tokens: 最大生成 token 数
            timeout: 单次调用超时时间（秒）

        Returns:
            解析后的 JSON 字典

        Raises:
            LLMInferenceError: JSON 解析失败
            LLMTimeoutError: 调用超时
        """
        content = await self.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            timeout=timeout,
        )
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMInferenceError(f"LLM 输出 JSON 解析失败: {exc}") from exc

    async def classify(
        self,
        system_prompt: str,
        user_input: str,
        *,
        model: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """意图分类专用接口

        使用 fallback_model（更小的 7B 模型）降低延迟，
        强制 json_mode 输出结构化分类结果。

        Args:
            system_prompt: 系统 prompt（含 few-shot 示例和输出格式约束）
            user_input: 用户输入文本
            model: 模型名称，None 时使用 fallback_model
            timeout: 单次调用超时时间（秒）

        Returns:
            分类结果字典，预期包含: intent, entities, sentiment, confidence
        """
        return await self.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            model=model or self._settings.fallback_model,
            temperature=0.1,
            max_tokens=512,
            timeout=timeout,
        )

    async def generate(
        self,
        system_prompt: str,
        user_input: str,
        context: str = "",
        *,
        history: list[dict[str, str]] | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """RAG 生成专用接口

        基于检索上下文和对话历史生成回复。

        Args:
            system_prompt: 系统 prompt
            user_input: 用户问题
            context: RAG 检索上下文
            history: 对话历史 [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}]
            model: 模型名称
            timeout: 单次调用超时（秒）, None 时用客户端默认 60s

        Returns:
            生成的回复文本
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]
        if history:
            # P1-5 第三轮修复: 移除 history[-6:] 二次截断 — 上游 _load_history 已按 token
            # 预算 + 重要性标记(投诉/承诺/转人工永不裁剪)精确保留, 这里再截 6 条会把
            # 5 轮前的关键轮次静默丢弃, 两处口径冲突 (token 预算 vs 固定条数).
            # 信任调用方的裁剪结果, 全量传入.
            messages.extend(history)
        if context:
            messages.append({"role": "system", "content": f"参考知识：\n{context}"})
        messages.append({"role": "user", "content": user_input})

        return await self.chat(
            messages,
            model=model or self._settings.primary_model,
            temperature=0.3,
            timeout=timeout,
        )

    async def health_check(self) -> bool:
        """检查 LLM 服务可用性"""
        try:
            response = await self._client.models.list()
            return len(response.data) > 0
        except Exception:
            return False

    @staticmethod
    def _record_usage(model: str, response: Any, *, method: str) -> None:
        """P0-6 第三轮修复: token 成本埋点 (此前 record_llm_usage 无生产调用,
        LLM_TOKEN_USAGE / LLM_COST_USD / LLM_BUDGET_* 指标永不产生数据, 预算熔断形同虚设).
        fire-and-forget, 失败不阻塞主流程.
        """
        try:
            usage = getattr(response, "usage", None)
            if usage is None:
                return
            from lumio.services.common.budget import record_llm_usage

            record_llm_usage(
                model=model,
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                method=method,
            )
        except Exception:
            pass
