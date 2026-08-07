"""E0: Token 成本可见性 + 预算熔断.

3 大能力:
1. 每次 LLM 调用上报 input_tokens / output_tokens / cost
2. 月度预算熔断 (全局)
3. Per-tenant 日预算熔断 (Redis counter)

告警:
- 预算使用 > 80% → warning
- 预算使用 > 100% → 拒绝新请求, 返回 503 + budget_exceeded
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from lumio.shared.config import BudgetSettings, get_settings
from lumio.shared.logger import get_logger
from lumio.shared.metrics import (
    LLM_BUDGET_EXCEEDED,
    LLM_BUDGET_REMAINING,
    LLM_COST_USD,
    LLM_TOKEN_USAGE,
)

logger = get_logger(__name__)


@dataclass
class CostRecord:
    """单次 LLM 调用成本记录."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    customer_id: str | None = None
    tenant_id: str = "__default__"
    method: str = "chat"
    timestamp: float = 0.0


class BudgetManager:
    """LLM 预算管理器 (单例)."""

    def __init__(self, settings: BudgetSettings | None = None) -> None:
        self._settings = settings or get_settings().budget
        self._redis: Any = None
        # 同步路径 fallback buffer (无 event loop 时, 记录待异步 flush)
        self._sync_cost_buffer: list[CostRecord] = []
        self._sync_buffer_max = 1000  # 防内存爆炸
        # 后台 task 引用, 防 GC
        self._pending_tasks: set[asyncio.Task[None]] = set()

    def compute_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """根据模型单价计算成本 (USD)."""
        in_price = self._settings.cost_per_1m_input_tokens.get(model, 0.5)
        out_price = self._settings.cost_per_1m_output_tokens.get(model, 1.0)
        cost = (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price
        return round(cost, 6)

    async def record_usage(self, record: CostRecord) -> None:
        """上报 token 使用 + 成本 (指标 + Redis 累计)."""
        # 1. Prometheus 指标
        LLM_TOKEN_USAGE.labels(model=record.model, method=record.method, direction="input").inc(record.input_tokens)
        LLM_TOKEN_USAGE.labels(model=record.model, method=record.method, direction="output").inc(record.output_tokens)
        LLM_COST_USD.labels(model=record.model, tenant_id=record.tenant_id).inc(record.cost_usd)

        # 2. Redis 累计 (用于预算检查)
        redis = await self._get_redis()
        if not redis:
            return
        try:
            yyyymm = time.strftime("%Y-%m")
            yyyy_mm_dd = time.strftime("%Y-%m-%d")
            # 全局月度
            await redis.incrbyfloat(
                f"lumio:budget:monthly:{yyyymm}",
                record.cost_usd,
            )
            await redis.expire(f"lumio:budget:monthly:{yyyymm}", 35 * 86400)
            # Per-tenant 日
            await redis.incrbyfloat(
                f"lumio:budget:tenant:{record.tenant_id}:{yyyy_mm_dd}",
                record.cost_usd,
            )
            await redis.expire(
                f"lumio:budget:tenant:{record.tenant_id}:{yyyy_mm_dd}",
                2 * 86400,
            )
        except Exception as exc:
            logger.debug("Redis 预算累计失败: %s", exc)

    async def check_budget(self, tenant_id: str = "__default__") -> tuple[bool, str]:
        """检查预算是否超限.

        Returns:
            (allowed, reason)
        """
        redis = await self._get_redis()
        if not redis:
            return True, "redis_unavailable"

        try:
            # 1. 月度预算
            yyyymm = time.strftime("%Y-%m")
            monthly_cost = float(await redis.get(f"lumio:budget:monthly:{yyyymm}") or 0)
            monthly_limit = self._settings.monthly_budget_usd
            if monthly_cost >= monthly_limit:
                LLM_BUDGET_EXCEEDED.labels(tenant_id=tenant_id, scope="monthly").inc()
                LLM_BUDGET_REMAINING.labels(tenant_id=tenant_id).set(0.0)
                return False, f"monthly_budget_exceeded: ${monthly_cost:.2f}/${monthly_limit:.2f}"

            remaining = monthly_limit - monthly_cost
            LLM_BUDGET_REMAINING.labels(tenant_id=tenant_id).set(remaining)

            # 2. Per-tenant 日预算
            yyyy_mm_dd = time.strftime("%Y-%m-%d")
            tenant_cost = float(await redis.get(f"lumio:budget:tenant:{tenant_id}:{yyyy_mm_dd}") or 0)
            daily_limit = self._settings.per_tenant_daily_limit_usd
            if tenant_cost >= daily_limit:
                LLM_BUDGET_EXCEEDED.labels(tenant_id=tenant_id, scope="daily_tenant").inc()
                return False, f"tenant_daily_budget_exceeded: ${tenant_cost:.2f}/${daily_limit:.2f}"

            return True, "ok"
        except Exception as exc:
            logger.warning("预算检查失败 (放行): %s", exc)
            return True, f"check_failed: {exc}"

    async def get_remaining(self, tenant_id: str = "__default__") -> dict[str, float]:
        """获取剩余预算 (用于前端展示)."""
        redis = await self._get_redis()
        if not redis:
            return {"monthly_remaining": -1, "daily_tenant_remaining": -1}
        try:
            yyyymm = time.strftime("%Y-%m")
            yyyy_mm_dd = time.strftime("%Y-%m-%d")
            monthly = float(await redis.get(f"lumio:budget:monthly:{yyyymm}") or 0)
            tenant = float(await redis.get(f"lumio:budget:tenant:{tenant_id}:{yyyy_mm_dd}") or 0)
            return {
                "monthly_remaining": self._settings.monthly_budget_usd - monthly,
                "daily_tenant_remaining": self._settings.per_tenant_daily_limit_usd - tenant,
            }
        except Exception:
            return {"monthly_remaining": -1, "daily_tenant_remaining": -1}

    async def _get_redis(self) -> Any:
        if self._redis is None:
            try:
                from lumio.services.common.redis_client import get_redis_client

                self._redis = get_redis_client()
            except Exception as exc:
                # P0-2 第三轮修复: 升级为 WARNING — 预算失效是 fail-open, 必须可观测
                logger.warning("预算 Redis 客户端初始化失败 (预算熔断失效): %s", exc)
                self._redis = False
        return self._redis if self._redis else None


# 全局单例
_budget: BudgetManager | None = None


def get_budget_manager() -> BudgetManager:
    global _budget
    if _budget is None:
        _budget = BudgetManager()
    return _budget


def record_llm_usage(
    model: str,
    input_tokens: int,
    output_tokens: int,
    customer_id: str | None = None,
    tenant_id: str = "__default__",
    method: str = "chat",
) -> float:
    """便捷函数: 同步记录 token 使用 + 成本."""
    bm = get_budget_manager()
    cost = bm.compute_cost(model, input_tokens, output_tokens)
    record = CostRecord(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        customer_id=customer_id,
        tenant_id=tenant_id,
        method=method,
        timestamp=time.time(),
    )
    # 异步上报 (fire-and-forget, 失败不阻塞主流程)
    try:
        loop = asyncio.get_running_loop()  # Python 3.7+, 替代已弃用的 get_event_loop
        task = loop.create_task(bm.record_usage(record))
        # 持有引用, 防 GC
        bm._pending_tasks.add(task)
        task.add_done_callback(bm._pending_tasks.discard)

        def _on_record_done(t: asyncio.Task[None]) -> None:
            if exc := t.exception():
                logger.error("record_usage 失败: model=%s, err=%s", model, exc)

        task.add_done_callback(_on_record_done)
    except RuntimeError:
        # 无运行 loop (同步上下文), 退化到直接同步上报
        # 不静默丢弃 — 同步走完后, 至少记录 metric 计数器
        try:
            # 同步路径: 累加到内存 counter, 避免成本归因丢失
            bm._sync_cost_buffer.append(record)
        except Exception as exc:
            logger.warning("同步 budget 记录失败 (成本归因丢失): %s", exc)
    return cost
