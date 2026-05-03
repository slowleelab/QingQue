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
from typing import Any

from openai import AsyncOpenAI

from smartcs.shared.config import LLMSettings, get_settings
from smartcs.shared.exceptions import LLMInferenceError, LLMTimeoutError

logger = logging.getLogger(__name__)


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
        if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
            return True
        return False

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
                logger.debug(
                    "LLM call succeeded: model=%s, latency_ms=%d, tokens=%d",
                    kwargs["model"],
                    int(elapsed * 1000),
                    response.usage.total_tokens if response.usage else 0,
                )
                return content
            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning("LLM 调用超时 (attempt %d/%d)", attempt + 1, max_retries)
            except Exception as exc:
                last_error = exc
                logger.warning("LLM 调用异常 (attempt %d/%d): %s", attempt + 1, max_retries, exc)

            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (2 ** attempt))

        self._breaker.record_failure()
        raise LLMTimeoutError(f"LLM 调用失败: {last_error}") from last_error

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
        model: str | None = None,
    ) -> str:
        """RAG 生成专用接口

        基于检索上下文生成回复。

        Args:
            system_prompt: 系统 prompt
            user_input: 用户问题
            context: RAG 检索上下文
            model: 模型名称

        Returns:
            生成的回复文本
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]
        if context:
            messages.append({"role": "system", "content": f"参考知识：\n{context}"})
        messages.append({"role": "user", "content": user_input})

        return await self.chat(
            messages,
            model=model or self._settings.primary_model,
            temperature=0.3,
        )

    async def health_check(self) -> bool:
        """检查 LLM 服务可用性"""
        try:
            response = await self._client.models.list()
            return len(response.data) > 0
        except Exception:
            return False
