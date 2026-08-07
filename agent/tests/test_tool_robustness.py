"""Tool 调用健壮性单元测试 (tool_robustness.py)"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from lumio.services.common.tool_robustness import (
    MCPReconnector,
    ToolQuotaGuard,
    async_retry,
    execute_tools_concurrent,
    get_quota_guard,
)

# ── async_retry ──


async def test_retry_success_first_try():
    """首次成功不重试"""
    calls = 0

    @async_retry(max_attempts=3)
    async def f():
        nonlocal calls
        calls += 1
        return "ok"

    assert await f() == "ok"
    assert calls == 1


async def test_retry_succeeds_after_retries():
    """失败后重试成功"""
    calls = 0

    @async_retry(max_attempts=3, base_delay=0.01)
    async def f():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("slow")
        return "ok"

    assert await f() == "ok"
    assert calls == 3


async def test_retry_exhausted_raises():
    """重试耗尽抛最后异常"""
    calls = 0

    @async_retry(max_attempts=2, base_delay=0.01)
    async def f():
        nonlocal calls
        calls += 1
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        await f()
    assert calls == 2


async def test_retry_non_retryable_exception():
    """非重试异常直接上抛"""
    calls = 0

    @async_retry(max_attempts=3)
    async def f():
        nonlocal calls
        calls += 1
        raise ValueError("business error")

    with pytest.raises(ValueError):
        await f()
    assert calls == 1  # 不重试


async def test_retry_fixed_delay():
    """非指数退避 (固定延迟)"""
    calls = 0

    @async_retry(max_attempts=2, base_delay=0.01, exponential=False)
    async def f():
        nonlocal calls
        calls += 1
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        await f()


async def test_retry_tool_name_metric():
    """tool_name 透传指标标签 (不抛即可)"""

    @async_retry(max_attempts=2, base_delay=0.01, tool_name="query_bill")
    async def f():
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        await f()


# ── execute_tools_concurrent ──


async def test_concurrent_success_all():
    """全部成功, 按输入顺序返回"""

    async def executor(name: str, args: dict):
        return {"name": name, **args}

    results = await execute_tools_concurrent([("a", {"x": 1}), ("b", {"y": 2})], executor)
    assert [r["tool_name"] for r in results] == ["a", "b"]
    assert all(r["error"] is None for r in results)


async def test_concurrent_failure_isolated():
    """单个失败不影响其他 (失败占位)"""

    async def executor(name: str, args: dict):
        if name == "bad":
            raise RuntimeError("boom")
        return "ok"

    results = await execute_tools_concurrent([("bad", {}), ("good", {})], executor)
    assert results[0]["error"] == "boom"
    assert results[0]["result"] is None
    assert results[1]["result"] == "ok"


async def test_concurrent_empty():
    """空列表 → 空结果"""
    assert await execute_tools_concurrent([], lambda n, a: None) == []


# ── ToolQuotaGuard ──


class _FakeQuotaRedis:
    def __init__(self) -> None:
        self.eval_calls: list[tuple] = []

    async def eval(self, script: str, num_keys: int, key: str, ttl: int):
        self.eval_calls.append((key, ttl))
        return "3"


async def test_quota_allowed():
    """未超限 → 放行 + 返回计数"""
    guard = ToolQuotaGuard()
    fake = _FakeQuotaRedis()
    guard._redis = fake
    allowed, count = await guard.check_and_increment("c1", "query_bill")
    assert allowed is True
    assert count == 3
    assert fake.eval_calls[0][1] == 3600 + 10  # window + 10


async def test_quota_exceeded():
    """超限 → 拒绝 + 计数"""
    guard = ToolQuotaGuard()

    class _Over:
        async def eval(self, script, num_keys, key, ttl):
            return "11"

    guard._redis = _Over()
    allowed, count = await guard.check_and_increment("c1", "query_bill", max_calls=10)
    assert allowed is False
    assert count == 11


async def test_quota_no_redis_fail_open(monkeypatch):
    """无 Redis → 放行"""
    import lumio.services.common.redis_client as rc

    def boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(rc, "get_redis_client", boom)
    guard = ToolQuotaGuard()
    allowed, count = await guard.check_and_increment("c1", "t")
    assert allowed is True
    assert count == 0


async def test_quota_redis_error_fail_open():
    """Redis 异常 → 放行"""
    guard = ToolQuotaGuard()

    class _Boom:
        async def eval(self, *a):
            raise RuntimeError("down")

    guard._redis = _Boom()
    allowed, count = await guard.check_and_increment("c1", "t")
    assert allowed is True
    assert count == 0


def test_get_quota_guard_singleton():
    """单例"""
    assert get_quota_guard() is get_quota_guard()


# ── MCPReconnector ──


async def test_reconnector_attach_and_stop():
    """attach + start + stop 生命周期"""
    r = MCPReconnector(reconnect_interval=0.01)
    mcp = MagicMock()
    mcp.health_check = AsyncMock(return_value=True)
    mcp.reconnect = AsyncMock(return_value=True)
    r.attach(mcp)
    await r.start()
    assert r._running is True
    assert r._loop_task is not None
    await asyncio.sleep(0.05)  # 循环跑几轮 (健康探测)
    assert mcp.health_check.await_count > 0
    await r.stop()
    assert r._running is False
    await asyncio.sleep(0.05)  # 循环醒来后退出
    assert r._loop_task is None or r._loop_task.done()
