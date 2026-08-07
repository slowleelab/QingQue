"""Token 成本预算单元测试 (budget.py)"""

from __future__ import annotations

import asyncio

import pytest

from lumio.services.common.budget import (
    BudgetManager,
    CostRecord,
    get_budget_manager,
    record_llm_usage,
)
from lumio.shared.config import BudgetSettings


def _make_settings(**overrides) -> BudgetSettings:
    base = dict(
        monthly_budget_usd=100.0,
        per_tenant_daily_limit_usd=10.0,
        cost_per_1m_input_tokens={"qwen": 0.5, "unknown": 0.5},
        cost_per_1m_output_tokens={"qwen": 1.0, "unknown": 1.0},
    )
    base.update(overrides)
    return BudgetSettings(**base)


# ── compute_cost ──


def test_compute_cost_known_model():
    """已知单价模型: input 1M*$0.5 + output 1M*$1.0"""
    bm = BudgetManager(_make_settings())
    cost = bm.compute_cost("qwen", 1_000_000, 1_000_000)
    assert cost == 1.5


def test_compute_cost_unknown_model_defaults():
    """未知模型用默认单价 (0.5/1.0 per 1M)"""
    bm = BudgetManager(_make_settings())
    cost = bm.compute_cost("no_such_model", 1000, 500)
    assert cost == pytest.approx((1000 / 1e6) * 0.5 + (500 / 1e6) * 1.0)


def test_compute_cost_zero_tokens():
    """零 token → 零成本"""
    bm = BudgetManager(_make_settings())
    assert bm.compute_cost("qwen", 0, 0) == 0.0


# ── record_usage (指标 + Redis 累计) ──


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, float] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def incrbyfloat(self, key: str, value: float) -> None:
        self.data[key] = self.data.get(key, 0.0) + value

    async def expire(self, key: str, ttl: int) -> None:
        self.expire_calls.append((key, ttl))

    async def get(self, key: str) -> str | None:
        return str(self.data[key]) if key in self.data else None


async def test_record_usage_writes_redis():
    """记录成本到月度 + per-tenant 日累计"""
    bm = BudgetManager(_make_settings())
    fake = _FakeRedis()
    bm._redis = fake

    await bm.record_usage(CostRecord(model="qwen", input_tokens=1000, output_tokens=500, cost_usd=0.001))
    assert len(fake.data) == 2  # monthly + tenant daily
    monthly_key = next(k for k in fake.data if "monthly" in k)
    assert fake.data[monthly_key] == pytest.approx(0.001)
    # TTL 设置
    assert any("monthly" in k for k, _ in fake.expire_calls)
    assert any("tenant" in k for k, _ in fake.expire_calls)


async def test_record_usage_no_redis(monkeypatch):
    """无 Redis 时仅记指标, 不抛异常"""
    import lumio.services.common.redis_client as rc

    def boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(rc, "get_redis_client", boom)
    bm = BudgetManager(_make_settings())
    await bm.record_usage(CostRecord(model="qwen", input_tokens=1, output_tokens=1, cost_usd=0.0))


async def test_record_usage_redis_error_soft():
    """Redis 异常被吞"""
    bm = BudgetManager(_make_settings())

    class _Boom:
        async def incrbyfloat(self, *a):
            raise RuntimeError("down")

        async def expire(self, *a):
            raise RuntimeError("down")

    bm._redis = _Boom()
    await bm.record_usage(CostRecord(model="qwen", input_tokens=1, output_tokens=1, cost_usd=0.0))


# ── check_budget ──


async def test_check_budget_ok():
    """预算未超 → 放行"""
    bm = BudgetManager(_make_settings())
    bm._redis = _FakeRedis()
    allowed, reason = await bm.check_budget("tenant-a")
    assert allowed is True
    assert reason == "ok"


async def test_check_budget_monthly_exceeded():
    """月度预算超限 → 拒绝"""
    import time

    bm = BudgetManager(_make_settings(monthly_budget_usd=10.0))
    fake = _FakeRedis()
    fake.data = {f"lumio:budget:monthly:{time.strftime('%Y-%m')}": 15.0}
    bm._redis = fake
    allowed, reason = await bm.check_budget("t1")
    assert allowed is False
    assert "monthly_budget_exceeded" in reason


async def test_check_budget_daily_exceeded():
    """per-tenant 日预算超限 → 拒绝"""
    import time

    bm = BudgetManager(_make_settings(per_tenant_daily_limit_usd=5.0))
    fake = _FakeRedis()
    fake.data = {f"lumio:budget:tenant:t1:{time.strftime('%Y-%m-%d')}": 6.0}
    bm._redis = fake
    allowed, reason = await bm.check_budget("t1")
    assert allowed is False
    assert "tenant_daily_budget_exceeded" in reason


async def test_check_budget_no_redis_fail_open(monkeypatch):
    """Redis 不可用 → fail-open 放行"""
    import lumio.services.common.redis_client as rc

    def boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(rc, "get_redis_client", boom)
    bm = BudgetManager(_make_settings())
    allowed, reason = await bm.check_budget()
    assert allowed is True
    assert reason == "redis_unavailable"


async def test_check_budget_redis_error_fail_open():
    """Redis 异常 → fail-open 放行"""
    bm = BudgetManager(_make_settings())

    class _Boom:
        async def get(self, key: str):
            raise RuntimeError("down")

    bm._redis = _Boom()
    allowed, reason = await bm.check_budget()
    assert allowed is True
    assert reason.startswith("check_failed")


# ── get_remaining ──


async def test_get_remaining_values():
    """剩余预算计算"""
    import time

    bm = BudgetManager(_make_settings(monthly_budget_usd=100.0, per_tenant_daily_limit_usd=10.0))
    fake = _FakeRedis()
    fake.data = {
        f"lumio:budget:monthly:{time.strftime('%Y-%m')}": 30.0,
        f"lumio:budget:tenant:t1:{time.strftime('%Y-%m-%d')}": 4.0,
    }
    bm._redis = fake
    remaining = await bm.get_remaining("t1")
    assert remaining["monthly_remaining"] == pytest.approx(70.0)
    assert remaining["daily_tenant_remaining"] == pytest.approx(6.0)


async def test_get_remaining_no_redis(monkeypatch):
    """无 Redis → -1 占位"""
    import lumio.services.common.redis_client as rc

    def boom():
        raise RuntimeError("no redis")

    monkeypatch.setattr(rc, "get_redis_client", boom)
    bm = BudgetManager(_make_settings())
    remaining = await bm.get_remaining()
    assert remaining == {"monthly_remaining": -1, "daily_tenant_remaining": -1}


async def test_get_remaining_error():
    """Redis 异常 → -1 占位"""
    bm = BudgetManager(_make_settings())

    class _Boom:
        async def get(self, key: str):
            raise RuntimeError("down")

    bm._redis = _Boom()
    remaining = await bm.get_remaining()
    assert remaining["monthly_remaining"] == -1


# ── record_llm_usage 便捷函数 ──


def test_record_llm_usage_in_loop():
    """有 event loop → fire-and-forget task 上报"""
    from lumio.services.common import budget as budget_mod

    bm = BudgetManager(_make_settings())
    budget_mod._budget = bm  # 注入单例

    bm._redis = _FakeRedis()

    async def _run():
        cost = record_llm_usage("qwen", 1000, 500, customer_id="c1", method="generate")
        assert cost > 0
        if bm._pending_tasks:
            await asyncio.gather(*list(bm._pending_tasks), return_exceptions=True)

    asyncio.run(_run())


def test_record_llm_usage_no_loop_sync_buffer():
    """无 event loop → 同步 fallback buffer"""
    from lumio.services.common import budget as budget_mod

    bm = BudgetManager(_make_settings())
    budget_mod._budget = bm
    cost = record_llm_usage("qwen", 100, 50)
    assert cost > 0
    assert len(bm._sync_cost_buffer) == 1
    assert bm._sync_cost_buffer[0].model == "qwen"


def test_get_budget_manager_singleton():
    """全局单例"""
    assert get_budget_manager() is get_budget_manager()
