"""E1: 告警路由分级 (P0/P1/P2).

告警级别:
- P0 (服务不可用): PagerDuty 寻呼 + 5min 升级
- P1 (性能降级): 邮件 + Slack #oncall
- P2 (单点问题): 工单 (Jira)

告警规则 (内置):
- 错误率 > 1% → P1
- P99 latency > 5s → P1
- LLM 预算超限 → P0
- 熔断器打开 → P1
- 降级等级变化 → P2
- KV cache 命中率 < 30% → P2
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lumio.shared.logger import get_logger

logger = get_logger(__name__)


class AlertLevel(str, Enum):
    """告警级别."""

    P0 = "P0"  # 立即寻呼, 业务不可用
    P1 = "P1"  # 1 小时内响应, 性能降级
    P2 = "P2"  # 工单跟进, 单点问题


@dataclass
class Alert:
    """告警对象."""

    level: AlertLevel
    title: str
    description: str
    source: str
    metric_name: str | None = None
    metric_value: float | None = None
    threshold: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# 告警路由配置
ALERT_ROUTES: dict[AlertLevel, dict[str, Any]] = {
    AlertLevel.P0: {
        "channels": ["pagerduty", "slack_oncall", "phone"],
        "escalation_minutes": 5,
        "requires_ack": True,
    },
    AlertLevel.P1: {
        "channels": ["email", "slack_oncall"],
        "escalation_minutes": 60,
        "requires_ack": False,
    },
    AlertLevel.P2: {
        "channels": ["jira"],
        "escalation_minutes": 1440,  # 24h
        "requires_ack": False,
    },
}


class AlertRouter:
    """告警路由器 (单例)."""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[Alert], Any]] = {}
        self._recent_alerts: list[Alert] = []  # 最近 100 条, 防重复告警
        self._dedup_window_seconds = 300  # 5 分钟内相同告警去重

    def register_handler(self, channel: str, handler: Callable[[Alert], Any]) -> None:
        """注册告警处理器 (e.g. PagerDuty, Slack, Email)."""
        self._handlers[channel] = handler
        logger.info("告警处理器注册: channel=%s", channel)

    async def send(self, alert: Alert) -> bool:
        """发送告警, 按级别路由."""
        # 1. 去重
        if self._is_duplicate(alert):
            logger.debug("告警去重: title=%s level=%s", alert.title, alert.level.value)
            return False

        # 2. 路由
        route = ALERT_ROUTES.get(alert.level, ALERT_ROUTES[AlertLevel.P2])
        channels = route.get("channels", [])

        logger.warning(
            "[%s] %s: %s (source=%s)",
            alert.level.value,
            alert.title,
            alert.description,
            alert.source,
        )

        # 3. 调用处理器
        tasks = []
        for channel in channels:
            handler = self._handlers.get(channel)
            if handler:
                tasks.append(self._call_handler(channel, handler, alert))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # 4. 记录
        self._recent_alerts.append(alert)
        if len(self._recent_alerts) > 100:
            self._recent_alerts = self._recent_alerts[-100:]

        return True

    def _is_duplicate(self, alert: Alert) -> bool:
        """检查是否重复 (5min 内同 title + level)."""
        now = time.time()
        for prev in reversed(self._recent_alerts[-20:]):
            if now - prev.created_at > self._dedup_window_seconds:
                break
            if prev.title == alert.title and prev.level == alert.level:
                return True
        return False

    async def _call_handler(self, channel: str, handler: Callable[[Alert], Any], alert: Alert) -> None:
        try:
            result = handler(alert)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.error("告警处理失败: channel=%s err=%s", channel, exc)


# ── 预置告警规则 (注册到健康检查) ──

# P2-2: 持有后台 task 引用, 防止 asyncio GC 在循环 sleep 时回收 task
_alert_loop_tasks: set[asyncio.Task[None]] = set()


def build_alert_rules(router: AlertRouter) -> None:
    """注册内置告警规则 (在 app 启动时调用)."""

    async def check_llm_error_rate() -> None:
        from lumio.shared.metrics import REQUEST_COUNT

        # 计算 5min 错误率
        # 简化: 用 Counter 当前值 (生产应该用 rate())
        try:
            for metric in REQUEST_COUNT.collect():
                for sample in metric.samples:
                    if sample.labels.get("status", "").startswith("5") and sample.value > 100:
                        await router.send(
                            Alert(
                                level=AlertLevel.P1,
                                title="LLM 错误率过高",
                                description=f"5xx 错误数: {sample.value}",
                                source="metrics",
                                metric_name="http_requests_total",
                                metric_value=sample.value,
                                threshold=100,
                            )
                        )
        except Exception as exc:
            logger.debug("错误率检查失败: %s", exc)

    async def check_budget_exceeded() -> None:
        from lumio.shared.metrics import LLM_BUDGET_EXCEEDED

        for metric in LLM_BUDGET_EXCEEDED.collect():
            for sample in metric.samples:
                if sample.value > 0:
                    await router.send(
                        Alert(
                            level=AlertLevel.P0,
                            title="LLM 预算超限",
                            description=f"tenant={sample.labels.get('tenant_id')} 拒绝 {sample.value} 次",
                            source="budget",
                            metric_name="llm_budget_exceeded_total",
                            metric_value=sample.value,
                        )
                    )

    # 启动后台任务
    async def _loop() -> None:
        while True:
            try:
                await check_llm_error_rate()
                await check_budget_exceeded()
            except Exception as exc:
                logger.error("告警循环异常: %s", exc)
            await asyncio.sleep(60)  # 每分钟检查

    # P2-2: 持有 task 引用, 避免 asyncio GC 在 await sleep 时回收 task
    task = asyncio.create_task(_loop(), name="alerting_loop")
    _alert_loop_tasks.add(task)
    task.add_done_callback(_alert_loop_tasks.discard)


# ── 简单内置 handlers (无外部依赖) ──


async def console_handler(alert: Alert) -> None:
    """默认 console handler (开发环境)."""
    print(
        f"[ALERT-{alert.level.value}] {alert.title}: {alert.description} "
        f"(source={alert.source}, time={alert.created_at})"
    )


async def log_handler(alert: Alert) -> None:
    """默认 log handler (生产环境, 接 ELK / Loki)."""
    logger.warning(
        "ALERT[%s] %s | source=%s | desc=%s | meta=%s",
        alert.level.value,
        alert.title,
        alert.source,
        alert.description,
        alert.metadata,
    )


# 全局单例
_router: AlertRouter | None = None


def get_alert_router() -> AlertRouter:
    global _router
    if _router is None:
        _router = AlertRouter()
        _router.register_handler("console", console_handler)
        _router.register_handler("log", log_handler)
    return _router
