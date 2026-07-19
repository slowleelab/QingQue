"""降级策略管理模块

HealthMonitor（主动探测+被动熔断融合）→ DegradationManager（生成降级编排）→ ContentDegrader（内容降级链）
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import ClassVar

from smartcs.shared.models import DegradationLevel, IntentLabel

logger = logging.getLogger(__name__)


@dataclass
class GenerateResult:
    """生成结果，包含内容和来源标记"""

    content: str
    source: str  # "llm" | "retrieval" | "template" | "fallback"


class HealthMonitor:
    """LLM 健康监控器

    融合主动探测和被动熔断两个信号源，计算当前降级级别。
    """

    def __init__(
        self,
        llm_client,
        breaker,
        probe_interval: float = 1.0,
        probe_max_interval: float = 30.0,
        probe_timeout: float = 5.0,
        fail_threshold: int = 2,
        success_threshold: int = 2,
    ) -> None:
        self._llm = llm_client
        self._breaker = breaker
        self._probe_interval = probe_interval
        self._max_interval = probe_max_interval
        self._probe_timeout = probe_timeout
        self._fail_threshold = fail_threshold
        self._success_threshold = success_threshold
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._current_interval = probe_interval
        # breaker 打开 → 初始 FALLBACK
        initial = DegradationLevel.FALLBACK if not breaker.is_available else DegradationLevel.NORMAL
        self._level: DegradationLevel = initial
        self._task: asyncio.Task | None = None

    @property
    def level(self) -> DegradationLevel:
        return self._level

    @property
    def is_llm_available(self) -> bool:
        return self._level == DegradationLevel.NORMAL

    async def start(self) -> None:
        """启动后台探测任务"""
        if self._task is None:
            self._task = asyncio.create_task(self._probe_loop())

    async def stop(self) -> None:
        """停止后台探测任务"""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _probe_loop(self) -> None:
        """后台探测循环"""
        while True:
            await asyncio.sleep(self._current_interval)
            await self._probe_once()

    async def _probe_once(self) -> None:
        """执行一次探测"""
        try:
            await self._llm.chat(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                timeout=self._probe_timeout,
            )
            self._consecutive_successes += 1
            self._consecutive_failures = 0
        except Exception:
            self._consecutive_failures += 1
            self._consecutive_successes = 0
        self._recompute_level()

    def _update_interval(self) -> None:
        """指数退避更新探测间隔"""
        n = self._consecutive_failures
        if n == 0:
            self._current_interval = self._probe_interval
        else:
            self._current_interval = min(2.0**n, self._max_interval)

    def _recompute_level(self) -> None:
        """根据连续成功/失败次数重新计算降级级别"""
        if self._consecutive_failures >= self._fail_threshold:
            self._level = DegradationLevel.DEGRADED
        elif self._consecutive_successes >= self._success_threshold:
            self._level = DegradationLevel.NORMAL
        self._update_interval()


class ContentDegrader:
    """内容降级链：检索摘要 → 按意图模板 → 硬编码兜底"""

    _TEMPLATES: ClassVar[dict[IntentLabel, str]] = {
        IntentLabel.BILL_QUERY: "抱歉，暂时无法查询您的账单信息。请输入「转人工」联系客服协助您查询账单。",
        IntentLabel.TRANSACTION_QUERY: "抱歉，暂时无法查询您的交易明细。请输入「转人工」联系客服协助您查询交易记录。",
        IntentLabel.LIMIT_QUERY: "抱歉，暂时无法查询您的额度信息。请输入「转人工」联系客服协助您处理额度问题。",
        IntentLabel.INSTALLMENT_INQUIRY: "抱歉，暂时无法回答您关于分期的问题。请输入「转人工」联系客服协助您了解分期详情。",
        IntentLabel.REWARD_QUERY: "抱歉，暂时无法查询您的积分信息。请输入「转人工」联系客服协助您查询积分。",
        IntentLabel.FAQ: "抱歉，我暂时无法回答您的问题。请尝试描述得更具体一些，或输入「转人工」联系客服。",
        IntentLabel.CARD_LOSS: "检测到您可能需要办理挂失业务，正在为您转接人工客服，请稍候。\n\n如需立即处理，请输入「转人工」直达人工坐席。\n\n转接原因：挂失业务",
        IntentLabel.COMPLAINT: "检测到您可能需要投诉处理，正在为您转接人工客服，请稍候。\n\n如需立即处理，请输入「转人工」直达人工坐席。\n\n转接原因：投诉处理",
        IntentLabel.TRANSFER_AGENT: "正在为您转接人工客服，请稍候。",
        IntentLabel.CHITCHAT: "抱歉，我暂时无法理解您的问题。您可以问我关于账单、额度、积分等方面的问题。",
    }

    def retrieval_summary(self, context: str, max_chars: int = 500) -> str:
        """将检索上下文拼接为可读摘要（智能截断）"""
        chunks = [c.strip() for c in context.split("\n\n") if c.strip()]
        if not chunks:
            return ""
        first = chunks[0]
        if len(first) > max_chars:
            last_period = first.rfind("。", 0, max_chars)
            last_newline = first.rfind("\n", 0, max_chars)
            cut = max(last_period, last_newline, max_chars - 10)
            first = first[: cut + 1]
        summary = f"根据相关信息：{first}"
        if len(chunks) > 1:
            summary += f"\n\n还有 {len(chunks) - 1} 条相关内容，如需了解请详细描述您的问题。"
        return summary

    def get_template(self, intent_label: IntentLabel | None) -> str:
        """按意图返回模板回复"""
        if intent_label and intent_label in self._TEMPLATES:
            return self._TEMPLATES[intent_label]
        return self.hardcoded_fallback()

    def hardcoded_fallback(self) -> str:
        """最后保障"""
        return "抱歉，服务暂时不可用，请稍后再试或拨打客服热线。"


class DegradationManager:
    """降级编排管理器

    generate 降级的统一入口，Agent 节点通过此接口调用。
    """

    def __init__(self, llm_client, health_monitor: HealthMonitor, content_degrader: ContentDegrader) -> None:
        self._llm = llm_client
        self._health_monitor = health_monitor
        self._degrader = content_degrader

    @property
    def level(self) -> DegradationLevel:
        return self._health_monitor.level

    async def generate_with_fallback(
        self,
        system_prompt: str,
        user_input: str,
        context: str = "",
        intent_label: IntentLabel | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> GenerateResult:
        """根据降级级别决定生成策略

        NORMAL:   LLM generate → 失败→检索摘要 → context空→模板
        DEGRADED: 跳过LLM → 检索摘要 → 模板
        FALLBACK: 跳过LLM → 模板
        """
        level = self._health_monitor.level

        # NORMAL: try LLM
        if level == DegradationLevel.NORMAL and self._llm.breaker.is_available:
            try:
                resp = await self._llm.generate(
                    system_prompt=system_prompt,
                    user_input=user_input,
                    context=context,
                    history=history,
                )
                return GenerateResult(content=resp, source="llm")
            except Exception:
                logger.warning("LLM generate 失败，进入内容降级")

        # DEGRADED or LLM failed: use retrieval if available (FALLBACK skips retrieval)
        if level != DegradationLevel.FALLBACK and context:
            return GenerateResult(
                content=self._degrader.retrieval_summary(context),
                source="retrieval",
            )

        # FALLBACK or no context: use template
        return GenerateResult(
            content=self._degrader.get_template(intent_label),
            source="template",
        )
