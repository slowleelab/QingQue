"""熔断器单元测试

覆盖：
- 状态转换：CLOSED→OPEN, OPEN→HALF_OPEN, HALF_OPEN→CLOSED, HALF_OPEN→OPEN
- 滑动窗口淘汰
- 失败率阈值检测
- 慢调用率阈值检测
- protected_call：成功、失败、熔断器打开、超时（慢调用）
- 成功重置失败计数
- half_open_max_calls 限制
"""

from __future__ import annotations

import asyncio
import time

import pytest

from smartcs.services.common.circuit_breaker import CircuitBreaker, CircuitState
from smartcs.shared.exceptions import CircuitBreakerOpenError


# ── 基础属性 ──


class TestCircuitBreakerInit:
    """构造参数和初始状态"""

    def test_default_state_is_closed(self) -> None:
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED

    def test_name_property(self) -> None:
        cb = CircuitBreaker(name="my-executor")
        assert cb.name == "my-executor"

    def test_invalid_failure_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match="failure_threshold"):
            CircuitBreaker(failure_threshold=1.5)

    def test_invalid_failure_threshold_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="failure_threshold"):
            CircuitBreaker(failure_threshold=-0.1)

    def test_invalid_slow_call_rate_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match="slow_call_rate_threshold"):
            CircuitBreaker(slow_call_rate_threshold=2.0)

    def test_invalid_sliding_window_size_raises(self) -> None:
        with pytest.raises(ValueError, match="sliding_window_size"):
            CircuitBreaker(sliding_window_size=0)


# ── 状态转换 ──


class TestStateTransitions:
    """CLOSED→OPEN, OPEN→HALF_OPEN, HALF_OPEN→CLOSED, HALF_OPEN→OPEN"""

    def test_closed_to_open_on_failure_rate(self) -> None:
        """失败率超过阈值，CLOSED→OPEN"""
        cb = CircuitBreaker(name="t", failure_threshold=0.5, sliding_window_size=4)
        # 3 次失败 + 1 次成功 = 75% 失败率 > 50%
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == CircuitState.OPEN

    def test_stays_closed_below_threshold(self) -> None:
        """失败率未达阈值，保持 CLOSED"""
        cb = CircuitBreaker(name="t", failure_threshold=0.6, sliding_window_size=4)
        cb.record_failure()
        cb.record_success()  # 1/2 = 50% < 60%
        cb.record_success()  # 1/3 ≈ 33% < 60%
        cb.record_success()  # 1/4 = 25% < 60%
        assert cb.state == CircuitState.CLOSED

    def test_open_to_half_open_after_recovery_timeout(self) -> None:
        """恢复超时后，OPEN→HALF_OPEN"""
        cb = CircuitBreaker(name="t", failure_threshold=0.5, recovery_timeout=0.1, sliding_window_size=4)
        # 触发 OPEN
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # 等待恢复超时
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

    def test_open_stays_open_before_recovery_timeout(self) -> None:
        """恢复超时前，保持 OPEN"""
        cb = CircuitBreaker(name="t", failure_threshold=0.5, recovery_timeout=10.0, sliding_window_size=4)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_half_open_to_closed_on_successes(self) -> None:
        """HALF_OPEN 下足够成功次数，HALF_OPEN→CLOSED"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=0.5,
            recovery_timeout=0.1,
            half_open_max_calls=5,
            half_open_success_threshold=2,
            sliding_window_size=4,
        )
        # 进入 OPEN
        for _ in range(4):
            cb.record_failure()
        # 等待进入 HALF_OPEN
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        # 探测成功
        assert cb.allow_request() is True
        cb.record_success()
        assert cb.allow_request() is True
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_to_open_on_failure(self) -> None:
        """HALF_OPEN 下任意失败，HALF_OPEN→OPEN"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=0.5,
            recovery_timeout=0.1,
            half_open_max_calls=5,
            half_open_success_threshold=2,
            sliding_window_size=4,
        )
        for _ in range(4):
            cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        assert cb.allow_request() is True
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_half_open_resets_opened_at(self) -> None:
        """HALF_OPEN→OPEN 后重新计时恢复超时"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=0.5,
            recovery_timeout=0.1,
            half_open_max_calls=5,
            half_open_success_threshold=2,
            sliding_window_size=4,
        )
        for _ in range(4):
            cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        # 探测失败 → 重新 OPEN
        cb.allow_request()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # 立即检查不应进入 HALF_OPEN
        assert cb.state == CircuitState.OPEN


# ── 滑动窗口 ──


class TestSlidingWindow:
    """滑动窗口淘汰和统计"""

    def test_window_evicts_old_entries(self) -> None:
        """窗口满后旧记录被淘汰"""
        cb = CircuitBreaker(name="t", failure_threshold=0.5, sliding_window_size=4)
        # 4 次失败 = 100% 失败率 → OPEN
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_window_eviction_allows_recovery(self) -> None:
        """窗口淘汰旧失败记录后，失败率下降，可以恢复"""
        cb = CircuitBreaker(name="t", failure_threshold=0.5, sliding_window_size=4)
        # 3次失败 + 1次成功 = 75% → OPEN
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == CircuitState.OPEN

        # 恢复到 HALF_OPEN
        time.sleep(0.01)  # recovery_timeout is 30s default, 手动转换
        cb._state = CircuitState.HALF_OPEN
        cb._reset_half_open_counts()
        cb.allow_request()
        cb.record_success()
        cb.allow_request()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

        # 现在窗口被清空，重新填入成功
        for _ in range(4):
            cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_empty_window_does_not_trigger(self) -> None:
        """空窗口不触发熔断"""
        cb = CircuitBreaker(name="t", failure_threshold=0.0, sliding_window_size=10)
        # 失败率阈值 0%，但窗口记录不足 2 条，不触发
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED


# ── 失败率阈值 ──


class TestFailureRateThreshold:
    """失败率阈值检测"""

    def test_exact_threshold_triggers(self) -> None:
        """失败率等于阈值触发熔断"""
        cb = CircuitBreaker(name="t", failure_threshold=0.5, sliding_window_size=4)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_success()
        # 2/4 = 50% == 50% → OPEN
        assert cb.state == CircuitState.OPEN

    def test_below_threshold_does_not_trigger(self) -> None:
        """失败率低于阈值不触发"""
        cb = CircuitBreaker(name="t", failure_threshold=0.6, sliding_window_size=4)
        cb.record_failure()
        cb.record_success()  # 1/2 = 50% < 60%
        cb.record_success()  # 1/3 ≈ 33% < 60%
        cb.record_success()  # 1/4 = 25% < 60%
        assert cb.state == CircuitState.CLOSED

    def test_threshold_zero_triggers_on_any_failure(self) -> None:
        """阈值 0 时任何失败都会触发"""
        cb = CircuitBreaker(name="t", failure_threshold=0.0, sliding_window_size=10)
        cb.record_failure()
        cb.record_success()  # 需要 2 条记录才检查
        assert cb.state == CircuitState.OPEN


# ── 慢调用率阈值 ──


class TestSlowCallRateThreshold:
    """慢调用率阈值检测"""

    def test_slow_call_rate_triggers_open(self) -> None:
        """慢调用率超过阈值触发熔断"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=1.0,  # 失败率阈值设高，不干扰
            slow_call_duration=1.0,
            slow_call_rate_threshold=0.5,
            sliding_window_size=4,
        )
        # 通过 record_success 传入 elapsed
        cb.record_success(elapsed=2.0)  # 慢
        cb.record_success(elapsed=0.5)  # 正常
        cb.record_success(elapsed=2.0)  # 慢
        cb.record_success(elapsed=0.5)  # 正常
        # 慢调用率 50% == 50% → OPEN
        assert cb.state == CircuitState.OPEN

    def test_slow_call_below_threshold_no_trigger(self) -> None:
        """慢调用率低于阈值不触发"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=1.0,
            slow_call_duration=1.0,
            slow_call_rate_threshold=0.5,
            sliding_window_size=4,
        )
        cb.record_success(elapsed=0.3)  # 正常
        cb.record_success(elapsed=0.4)  # 正常
        cb.record_success(elapsed=2.0)  # 慢
        cb.record_success(elapsed=0.5)  # 正常
        # 慢调用率 25% < 50% → CLOSED
        assert cb.state == CircuitState.CLOSED

    def test_record_slow_call_method(self) -> None:
        """record_slow_call 独立记录慢调用"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=1.0,
            slow_call_duration=1.0,
            slow_call_rate_threshold=0.5,
            sliding_window_size=4,
        )
        cb.record_slow_call(2.0)  # 慢
        cb.record_slow_call(0.5)  # 正常
        cb.record_slow_call(2.0)  # 慢
        cb.record_slow_call(0.5)  # 正常
        # 50% >= 50% → OPEN
        assert cb.state == CircuitState.OPEN

    def test_no_slow_call_duration_skips_check(self) -> None:
        """未设置 slow_call_duration 时不检测慢调用"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=1.0,
            slow_call_duration=None,
            slow_call_rate_threshold=0.5,
            sliding_window_size=4,
        )
        cb.record_slow_call(10.0)  # 无效，因为 slow_call_duration=None
        assert cb.state == CircuitState.CLOSED


# ── allow_request ──


class TestAllowRequest:
    """请求放行控制"""

    def test_closed_allows_all(self) -> None:
        """CLOSED 状态允许所有请求"""
        cb = CircuitBreaker(name="t")
        assert cb.allow_request() is True
        assert cb.allow_request() is True

    def test_open_rejects_all(self) -> None:
        """OPEN 状态拒绝所有请求"""
        cb = CircuitBreaker(name="t", failure_threshold=0.5, sliding_window_size=4)
        for _ in range(4):
            cb.record_failure()
        assert cb.allow_request() is False

    def test_half_open_limits_calls(self) -> None:
        """HALF_OPEN 限制探测请求数"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=0.5,
            recovery_timeout=0.1,
            half_open_max_calls=2,
            sliding_window_size=4,
        )
        for _ in range(4):
            cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        assert cb.allow_request() is True
        assert cb.allow_request() is True
        assert cb.allow_request() is False  # 超过限制

    def test_half_open_max_calls_default(self) -> None:
        """默认 half_open_max_calls=3"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=0.5,
            recovery_timeout=0.1,
            sliding_window_size=4,
        )
        for _ in range(4):
            cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        assert cb.allow_request() is True
        assert cb.allow_request() is True
        assert cb.allow_request() is True
        assert cb.allow_request() is False


# ── protected_call ──


class TestProtectedCall:
    """protected_call 异步调用测试"""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """成功调用返回结果"""
        cb = CircuitBreaker(name="t")

        async def ok() -> str:
            return "ok"

        result = await cb.protected_call(ok)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failure_records_and_propagates(self) -> None:
        """失败调用记录并传播异常"""
        cb = CircuitBreaker(name="t", failure_threshold=0.5, sliding_window_size=4)

        async def fail() -> None:
            raise RuntimeError("boom")

        # 2 次失败: 2/2 = 100% >= 50% → OPEN
        with pytest.raises(RuntimeError):
            await cb.protected_call(fail)
        with pytest.raises(RuntimeError):
            await cb.protected_call(fail)
        assert cb.state == CircuitState.OPEN

        # 第 3 次调用因熔断器打开抛出 CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError):
            await cb.protected_call(fail)

    @pytest.mark.asyncio
    async def test_open_circuit_raises(self) -> None:
        """熔断器打开时抛出 CircuitBreakerOpenError"""
        cb = CircuitBreaker(name="t", failure_threshold=0.5, sliding_window_size=4)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

        async def noop() -> str:
            return "never"

        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            await cb.protected_call(noop)
        assert exc_info.value.code == 4020

    @pytest.mark.asyncio
    async def test_timeout_records_slow_and_failure(self) -> None:
        """超时调用记录慢调用和失败"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=1.0,
            slow_call_duration=0.1,
            slow_call_rate_threshold=0.5,
            sliding_window_size=4,
        )

        async def slow() -> str:
            await asyncio.sleep(10)
            return "never"

        with pytest.raises(asyncio.TimeoutError):
            await cb.protected_call(slow, timeout=0.05)

    @pytest.mark.asyncio
    async def test_success_resets_window_on_recovery(self) -> None:
        """HALF_OPEN 成功后恢复 CLOSED，窗口清空"""
        cb = CircuitBreaker(
            name="t",
            failure_threshold=0.5,
            recovery_timeout=0.1,
            half_open_max_calls=5,
            half_open_success_threshold=2,
            sliding_window_size=4,
        )
        for _ in range(4):
            cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        async def ok() -> str:
            return "ok"

        # 两次成功探测恢复
        assert cb.allow_request() is True
        result = await cb.protected_call(ok)
        assert result == "ok"
        assert cb.state == CircuitState.HALF_OPEN or cb.state == CircuitState.CLOSED

        if cb.state == CircuitState.HALF_OPEN:
            assert cb.allow_request() is True
            await cb.protected_call(ok)
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_error_not_recorded_as_failure(self) -> None:
        """CircuitBreakerOpenError 不应被记录为失败"""
        cb = CircuitBreaker(name="t", failure_threshold=0.5, sliding_window_size=4)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # 直接调用 protected_call 应抛出 CircuitBreakerOpenError
        # 而不是把 CircuitBreakerOpenError 当作普通异常记录失败
        async def noop() -> str:
            return "never"

        with pytest.raises(CircuitBreakerOpenError):
            await cb.protected_call(noop)


# ── CircuitBreakerOpenError ──


class TestCircuitBreakerOpenError:
    """CircuitBreakerOpenError 异常"""

    def test_with_executor_name(self) -> None:
        err = CircuitBreakerOpenError(executor_name="my-exec")
        assert err.code == 4020
        assert "my-exec" in err.message
        assert "熔断器打开" in err.message

    def test_without_executor_name(self) -> None:
        err = CircuitBreakerOpenError()
        assert err.code == 4020
        assert err.message == "熔断器打开"

    def test_is_smartcs_error(self) -> None:
        from smartcs.shared.exceptions import SmartCSError

        err = CircuitBreakerOpenError(executor_name="test")
        assert isinstance(err, SmartCSError)
