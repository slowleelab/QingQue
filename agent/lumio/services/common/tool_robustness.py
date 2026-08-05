"""F1: Tool 调用健壮性 — 并发 + 重试 + 配额.

4 大能力:
1. asyncio.gather 并发执行多 tool (替代串行 for)
2. @async_retry 指数退避重试
3. Per-customer tool 配额 (Redis counter)
4. MCP 后端自动重连

熔断器集成:
- tool 级熔断 (1 个 tool 连续失败不阻塞其他 tool)
- 整体熔断 (MCP 后端不可用, 走降级链)
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from lumio.shared.logger import get_logger
from lumio.shared.metrics import (
    MCP_RECONNECT_ATTEMPTS,
    TOOL_CALL_DURATION,
    TOOL_QUOTA_EXCEEDED,
    TOOL_RETRIES,
)

logger = get_logger(__name__)

T = TypeVar("T")


# ── 异步重试装饰器 ──

def async_retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 5.0,
    exponential: bool = True,
    retry_on: tuple[type[Exception], ...] = (TimeoutError, ConnectionError, asyncio.TimeoutError),
    tool_name: str | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """异步重试装饰器 (指数退避).

    用法:
        @async_retry(max_attempts=3, tool_name="query_bill")
        async def call_tool(...):
            ...
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except retry_on as exc:
                    last_exc = exc
                    if attempt < max_attempts - 1:
                        delay = min(
                            base_delay * (2**attempt if exponential else 1), max_delay
                        )
                        TOOL_RETRIES.labels(
                            tool_name=tool_name or func.__name__, reason=type(exc).__name__
                        ).inc()
                        logger.warning(
                            "Tool %s 重试 %d/%d: %s, delay=%.2fs",
                            tool_name or func.__name__,
                            attempt + 1,
                            max_attempts,
                            exc,
                            delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "Tool %s 重试 %d 次仍失败: %s",
                            tool_name or func.__name__,
                            max_attempts,
                            exc,
                        )
            if last_exc:
                raise last_exc
            raise RuntimeError("retry loop exited without exception")

        return wrapper

    return decorator


# ── 并发执行多 tool (失败隔离) ──

async def execute_tools_concurrent(
    tools: list[tuple[str, dict[str, Any]]],
    executor: Callable[[str, dict[str, Any]], Awaitable[Any]],
) -> list[dict[str, Any]]:
    """并发执行多个 tool 调用, **内部 try/except 隔离**.

    失败隔离由 `_run_one` 内部 try/except 保证 (异常转成 {"error": ...}),
    gather 不需要 return_exceptions=True — 任一 tool 异常都被吞, 不会让其他成功结果丢失.

    Args:
        tools: [(tool_name, tool_args), ...]
        executor: 实际执行 tool 的函数 (e.g. mcp_client.call_tool)

    Returns:
        [{"tool_name": ..., "result": ..., "error": ...}, ...]
        按输入顺序返回, 失败的 tool 用 {"error": ...} 占位
    """
    async def _run_one(name: str, args: dict[str, Any]) -> dict[str, Any]:
        _start = time.monotonic()
        try:
            result = await executor(name, args)
            elapsed = time.monotonic() - _start
            TOOL_CALL_DURATION.labels(tool_name=name, status="success").observe(elapsed)
            return {"tool_name": name, "result": result, "error": None}
        except Exception as exc:
            elapsed = time.monotonic() - _start
            TOOL_CALL_DURATION.labels(tool_name=name, status="error").observe(elapsed)
            logger.warning("Tool %s 执行失败: %s", name, exc)
            return {"tool_name": name, "result": None, "error": str(exc)}

    # asyncio.gather 失败隔离
    tasks = [_run_one(name, args) for name, args in tools]
    return await asyncio.gather(*tasks, return_exceptions=False)


# ── Per-customer tool 配额 ──

class ToolQuotaGuard:
    """Tool 调用配额守卫 (Redis counter)."""

    def __init__(self) -> None:
        self._redis: Any = None

    async def check_and_increment(
        self,
        customer_id: str,
        tool_name: str,
        window_seconds: int = 3600,
        max_calls: int = 10,
    ) -> tuple[bool, int]:
        """检查 + 增加配额计数.

        Returns:
            (allowed, current_count)
        """
        redis = await self._get_redis()
        if not redis:
            return True, 0

        # 每 customer_id + tool_name 一分钟一个 key
        window = window_seconds
        bucket = int(time.time() // window)
        key = f"lumio:tool:quota:{customer_id}:{tool_name}:{bucket}"

        try:
            # 使用 Lua 脚本原子执行 INCR + EXPIRE, 避免 incr 后崩溃导致 key 永不过期
            # 返回新计数, 同时确保首次写入时设过期时间
            incr_with_expire = """
            local current = redis.call('INCR', KEYS[1])
            if current == 1 then
                redis.call('EXPIRE', KEYS[1], ARGV[1])
            end
            return current
            """
            count_raw = await redis.eval(incr_with_expire, 1, key, window + 10)
            count = int(count_raw) if count_raw is not None else 0
            if count > max_calls:
                TOOL_QUOTA_EXCEEDED.labels(
                    tool_name=tool_name, scope="customer_window"
                ).inc()
                logger.warning(
                    "Tool 配额超限: customer=%s tool=%s count=%d max=%d",
                    customer_id,
                    tool_name,
                    count,
                    max_calls,
                )
                return False, count
            return True, count
        except Exception as exc:
            logger.debug("配额检查失败 (放行): %s", exc)
            return True, 0

    async def _get_redis(self) -> Any:
        if self._redis is None:
            try:
                from lumio.services.common.redis_client import get_redis_client

                self._redis = get_redis_client()
            except Exception as exc:
                # P0-2 第三轮修复: 升级为 WARNING — 配额失效是静默放行, 必须可观测
                logger.warning("配额 Redis 客户端初始化失败 (配额将放行): %s", exc)
                self._redis = False
        return self._redis if self._redis else None


# 全局单例
_quota_guard: ToolQuotaGuard | None = None


def get_quota_guard() -> ToolQuotaGuard:
    global _quota_guard
    if _quota_guard is None:
        _quota_guard = ToolQuotaGuard()
    return _quota_guard


# ── MCP 自动重连后台任务 ──

class MCPReconnector:
    """MCP 后端自动重连器 (后台任务)."""

    def __init__(self, reconnect_interval: float = 30.0) -> None:
        self.reconnect_interval = reconnect_interval
        self._running = False
        self._mcp_client: Any = None
        self._loop_task: asyncio.Task[None] | None = None  # 持有引用, 防 GC

    def attach(self, mcp_client: Any) -> None:
        self._mcp_client = mcp_client

    async def start(self) -> None:
        """启动后台重连循环."""
        self._running = True
        self._loop_task = asyncio.create_task(self._loop())
        logger.info("MCPReconnector 启动, interval=%.0fs", self.reconnect_interval)

    async def stop(self) -> None:
        self._running = False

    async def _loop(self) -> None:
        backoff = 1.0
        while self._running:
            await asyncio.sleep(self.reconnect_interval)
            if not self._mcp_client:
                continue
            try:
                # 探测后端健康
                healthy = await self._mcp_client.health_check()
                if not healthy:
                    logger.info("MCP 后端不健康, 尝试重连")
                    success = await self._mcp_client.reconnect()
                    MCP_RECONNECT_ATTEMPTS.labels(
                        server_name="default", result="success" if success else "failed"
                    ).inc()
                    if success:
                        backoff = 1.0
                    else:
                        backoff = min(backoff * 2, 60.0)
                else:
                    backoff = 1.0
            except Exception as exc:
                logger.warning("MCP 重连异常: %s", exc)
                MCP_RECONNECT_ATTEMPTS.labels(
                    server_name="default", result="failed"
                ).inc()
