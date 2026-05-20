"""通用熔断器模块

基于失败率和慢调用率的双重检测熔断器，支持：
- 滑动窗口统计失败率
- 慢调用率独立检测
- CLOSED / OPEN / HALF_OPEN 三态自动转换
- HALF_OPEN 限量探测恢复

对应概要设计 §6.2 执行器熔断策略
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any

from smartcs.shared.exceptions import CircuitBreakerOpenError

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """熔断器状态"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """通用熔断器

    Args:
        name: 熔断器名称，用于日志和异常信息
        failure_threshold: 失败率阈值 (0.0-1.0)，滑动窗口内失败率超过此值触发熔断
        recovery_timeout: 熔断恢复超时（秒），OPEN 状态持续时间后自动进入 HALF_OPEN
        half_open_max_calls: HALF_OPEN 状态最大允许探测请求数
        half_open_success_threshold: HALF_OPEN 状态连续成功次数达到此值后恢复 CLOSED
        slow_call_duration: 慢调用判定时长（秒），超过此时间视为慢调用
        slow_call_rate_threshold: 慢调用率阈值 (0.0-1.0)，超过此值触发熔断
        sliding_window_size: 滑动窗口大小（记录数量）
    """

    def __init__(
        self,
        name: str = "",
        failure_threshold: float = 0.5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
        half_open_success_threshold: int = 2,
        slow_call_duration: float | None = None,
        slow_call_rate_threshold: float = 0.5,
        sliding_window_size: int = 20,
    ) -> None:
        if not 0.0 <= failure_threshold <= 1.0:
            raise ValueError("failure_threshold 必须在 0.0 到 1.0 之间")
        if not 0.0 <= slow_call_rate_threshold <= 1.0:
            raise ValueError("slow_call_rate_threshold 必须在 0.0 到 1.0 之间")
        if sliding_window_size < 1:
            raise ValueError("sliding_window_size 必须大于 0")

        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls
        self._half_open_success_threshold = half_open_success_threshold
        self._slow_call_duration = slow_call_duration
        self._slow_call_rate_threshold = slow_call_rate_threshold
        self._sliding_window_size = sliding_window_size

        self._state = CircuitState.CLOSED
        self._opened_at: float = 0.0

        # 滑动窗口：True=成功, False=失败
        self._window: deque[bool] = deque(maxlen=sliding_window_size)
        # 慢调用窗口：True=慢调用, False=正常调用
        self._slow_window: deque[bool] = deque(maxlen=sliding_window_size)

        # HALF_OPEN 状态计数器
        self._half_open_calls: int = 0
        self._half_open_successes: int = 0

    @property
    def name(self) -> str:
        """熔断器名称"""
        return self._name

    @property
    def state(self) -> CircuitState:
        """当前状态，自动检查 OPEN→HALF_OPEN 超时转换"""
        if self._state == CircuitState.OPEN and time.monotonic() - self._opened_at >= self._recovery_timeout:
            self._transition_to_half_open()
        return self._state

    @property
    def failure_threshold(self) -> float:
        return self._failure_threshold

    @property
    def recovery_timeout(self) -> float:
        return self._recovery_timeout

    def allow_request(self) -> bool:
        """判断是否允许请求通过

        Returns:
            True 允许，False 拒绝
        """
        current = self.state  # 触发自动状态转换
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.HALF_OPEN:
            if self._half_open_calls < self._half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False
        # OPEN
        return False

    def record_success(self, elapsed: float | None = None) -> None:
        """记录成功调用

        Args:
            elapsed: 调用耗时（秒），用于慢调用判定
        """
        self._window.append(True)

        # 慢调用判定
        if self._slow_call_duration is not None and elapsed is not None:
            is_slow = elapsed >= self._slow_call_duration
            self._slow_window.append(is_slow)
            if is_slow:
                self._check_slow_call_rate()

        # 成功也可能使窗口中失败率仍然超阈值（例如少量失败后窗口未满）
        if self._state == CircuitState.CLOSED:
            self._check_failure_rate()

        if self._state == CircuitState.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self._half_open_success_threshold:
                self._transition_to_closed()
                logger.info("熔断器 [%s] HALF_OPEN→CLOSED，探测成功次数达标", self._name)

    def record_failure(self) -> None:
        """记录失败调用"""
        self._window.append(False)

        if self._state == CircuitState.HALF_OPEN:
            # HALF_OPEN 下任何失败立即回到 OPEN
            self._transition_to_open()
            logger.warning("熔断器 [%s] HALF_OPEN→OPEN，探测请求失败", self._name)
        elif self._state == CircuitState.CLOSED:
            self._check_failure_rate()

    def record_slow_call(self, elapsed: float) -> None:
        """记录慢调用

        Args:
            elapsed: 调用耗时（秒）
        """
        if self._slow_call_duration is None:
            return
        is_slow = elapsed >= self._slow_call_duration
        self._slow_window.append(is_slow)
        if is_slow:
            self._check_slow_call_rate()

    async def protected_call(
        self,
        fn: Callable[..., Coroutine[Any, Any, Any]],
        timeout: float | None = None,
    ) -> Any:
        """熔断保护的异步调用

        Args:
            fn: 异步可调用对象
            timeout: 超时时间（秒），超时视为慢调用并记录失败

        Returns:
            fn 的返回值

        Raises:
            CircuitBreakerOpenError: 熔断器处于 OPEN 状态
        """
        if not self.allow_request():
            raise CircuitBreakerOpenError(executor_name=self._name)

        start = time.monotonic()
        try:
            if timeout is not None:
                result = await asyncio.wait_for(fn(), timeout=timeout)
            else:
                result = await fn()
            elapsed = time.monotonic() - start
            self.record_success(elapsed=elapsed)
            return result
        except TimeoutError:
            elapsed = time.monotonic() - start
            # 超时记录为失败 + 慢调用
            self.record_failure()
            if self._slow_call_duration is not None:
                self.record_slow_call(elapsed)
            raise
        except CircuitBreakerOpenError:
            raise
        except Exception:
            self.record_failure()
            raise

    # ── 内部方法 ──

    def _check_failure_rate(self) -> None:
        """检查失败率是否超过阈值"""
        if len(self._window) < 2:
            return
        failure_count = sum(1 for ok in self._window if not ok)
        failure_rate = failure_count / len(self._window)
        if failure_rate >= self._failure_threshold:
            self._transition_to_open()
            logger.warning(
                "熔断器 [%s] CLOSED→OPEN，失败率 %.1f%% 超过阈值 %.1f%%",
                self._name,
                failure_rate * 100,
                self._failure_threshold * 100,
            )

    def _check_slow_call_rate(self) -> None:
        """检查慢调用率是否超过阈值"""
        if len(self._slow_window) < 2:
            return
        slow_count = sum(1 for s in self._slow_window if s)
        slow_rate = slow_count / len(self._slow_window)
        if slow_rate >= self._slow_call_rate_threshold:
            self._transition_to_open()
            logger.warning(
                "熔断器 [%s] 慢调用率 %.1f%% 超过阈值 %.1f%%，触发熔断",
                self._name,
                slow_rate * 100,
                self._slow_call_rate_threshold * 100,
            )

    def _transition_to_open(self) -> None:
        """转换到 OPEN 状态"""
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._reset_half_open_counts()

    def _transition_to_half_open(self) -> None:
        """转换到 HALF_OPEN 状态"""
        self._state = CircuitState.HALF_OPEN
        self._reset_half_open_counts()
        logger.info("熔断器 [%s] OPEN→HALF_OPEN，恢复超时已到，开始探测", self._name)

    def _transition_to_closed(self) -> None:
        """转换到 CLOSED 状态"""
        self._state = CircuitState.CLOSED
        self._reset_half_open_counts()
        self._window.clear()
        self._slow_window.clear()

    def _reset_half_open_counts(self) -> None:
        """重置 HALF_OPEN 计数器"""
        self._half_open_calls = 0
        self._half_open_successes = 0
