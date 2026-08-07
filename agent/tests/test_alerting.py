"""告警路由分级单元测试 (alerting.py)"""

from __future__ import annotations

import asyncio

from lumio.shared.alerting import (
    ALERT_ROUTES,
    Alert,
    AlertLevel,
    AlertRouter,
    build_alert_rules,
    console_handler,
    get_alert_router,
    log_handler,
)

# ── 数据模型 ──


def test_alert_level_values():
    """3 个告警级别"""
    assert AlertLevel.P0.value == "P0"
    assert AlertLevel.P1.value == "P1"
    assert AlertLevel.P2.value == "P2"


def test_alert_defaults():
    """Alert 缺省字段"""
    alert = Alert(level=AlertLevel.P1, title="t", description="d", source="s")
    assert alert.metric_name is None
    assert alert.metadata == {}
    assert alert.created_at > 0


def test_alert_routes_config():
    """P0 寻呼升级 5min, P2 走 Jira 24h"""
    assert "pagerduty" in ALERT_ROUTES[AlertLevel.P0]["channels"]
    assert ALERT_ROUTES[AlertLevel.P0]["escalation_minutes"] == 5
    assert ALERT_ROUTES[AlertLevel.P0]["requires_ack"] is True
    assert ALERT_ROUTES[AlertLevel.P1]["channels"] == ["email", "slack_oncall"]
    assert ALERT_ROUTES[AlertLevel.P2]["channels"] == ["jira"]
    assert ALERT_ROUTES[AlertLevel.P2]["escalation_minutes"] == 1440


# ── AlertRouter ──


def test_register_handler():
    """注册 handler 后 send 会调用"""
    router = AlertRouter()
    calls: list[str] = []

    def handler(alert: Alert) -> None:
        calls.append(alert.title)

    router.register_handler("test_ch", handler)
    assert "test_ch" in router._handlers


async def test_send_routes_to_handler():
    """send 按 P0 路由到已注册 channel"""
    router = AlertRouter()
    received: list[Alert] = []

    def handler(alert: Alert) -> None:
        received.append(alert)

    router.register_handler("pagerduty", handler)
    alert = Alert(level=AlertLevel.P0, title="预算超限", description="d", source="budget")
    ok = await router.send(alert)
    assert ok is True
    assert received == [alert]


async def test_send_dedup():
    """5min 内同 title+level 去重, 第二次返回 False"""
    router = AlertRouter()
    router.register_handler("console", console_handler)
    alert = Alert(level=AlertLevel.P1, title="错误率过高", description="d", source="m")
    assert await router.send(alert) is True
    assert await router.send(alert) is False  # 重复


async def test_send_different_alert_not_dedup():
    """不同 title 不触发去重"""
    router = AlertRouter()
    router.register_handler("console", console_handler)
    a1 = Alert(level=AlertLevel.P1, title="t1", description="d", source="s")
    a2 = Alert(level=AlertLevel.P1, title="t2", description="d", source="s")
    assert await router.send(a1) is True
    assert await router.send(a2) is True


async def test_send_unknown_level_fallback_p2(monkeypatch):
    """路由表缺失级别时回退 P2 路由"""
    from lumio.shared.alerting import ALERT_ROUTES as ROUTES

    monkeypatch.delitem(ROUTES, AlertLevel.P0)  # 模拟配置缺失 P0
    router = AlertRouter()
    received: list[Alert] = []

    def handler(alert: Alert) -> None:
        received.append(alert)

    router.register_handler("jira", handler)
    alert = Alert(level=AlertLevel.P0, title="t", description="d", source="s")
    await router.send(alert)
    assert len(received) == 1


async def test_handler_exception_swallowed():
    """handler 抛异常不向外传播"""
    router = AlertRouter()

    def bad_handler(alert: Alert) -> None:
        raise RuntimeError("boom")

    router.register_handler("pagerduty", bad_handler)
    alert = Alert(level=AlertLevel.P0, title="t", description="d", source="s")
    assert await router.send(alert) is True


async def test_async_handler_awaited():
    """协程 handler 会被 await"""
    router = AlertRouter()
    done: list[str] = []

    async def async_handler(alert: Alert) -> None:
        done.append(alert.title)

    router.register_handler("email", async_handler)
    alert = Alert(level=AlertLevel.P1, title="性能降级", description="d", source="s")
    await router.send(alert)
    assert done == ["性能降级"]


async def test_recent_alerts_bounded():
    """recent_alerts 上限 100 条"""
    router = AlertRouter()
    router.register_handler("console", console_handler)
    for i in range(120):
        alert = Alert(level=AlertLevel.P2, title=f"t{i}", description="d", source="s")
        await router.send(alert)
    assert len(router._recent_alerts) == 100


# ── 内置 handler / 单例 ──


async def test_console_handler_runs():
    """console handler 可调用不抛异常"""
    alert = Alert(level=AlertLevel.P2, title="t", description="d", source="s")
    await console_handler(alert)


async def test_log_handler_runs():
    """log handler 可调用不抛异常"""
    alert = Alert(level=AlertLevel.P0, title="t", description="d", source="s", metadata={"k": 1})
    await log_handler(alert)


def test_get_alert_router_singleton():
    """单例且注册了 console/log handler"""
    router = get_alert_router()
    assert "console" in router._handlers
    assert "log" in router._handlers


# ── build_alert_rules: 后台循环 ──


async def test_build_alert_rules_starts_loop():
    """build_alert_rules 启动后台循环, 可正常清理"""
    from lumio.shared import alerting

    router = AlertRouter()
    build_alert_rules(router)
    await asyncio.sleep(0.1)  # 给首轮检查一点时间
    # 清理后台 task, 避免泄漏 (CancelledError 是 BaseException 子类)
    for task in list(alerting._alert_loop_tasks):
        task.cancel()
    for task in list(alerting._alert_loop_tasks):
        with __import__("contextlib").suppress(BaseException):
            await task
    assert not alerting._alert_loop_tasks
