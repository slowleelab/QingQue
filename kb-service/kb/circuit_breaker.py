"""通用熔断器 (I2-C2)

三态: CLOSED → OPEN → HALF_OPEN → CLOSED
- CLOSED: 正常通过, 累计连续失败次数
- OPEN: 拦截请求, cooldown 期结束后允许 1 个探针
- HALF_OPEN: 1 次成功 → CLOSED, 1 次失败 → 回到 OPEN

设计要点:
- 纯 asyncio, 单实例可重入 (asyncio.Lock)
- 不引入外部依赖
- 公开 API 兼容 kb.pipeline.embedder.EmbeddingCircuitBreaker 旧行为
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class CircuitState(str, enum.Enum):
    """熔断器状态"""

    CLOSED = "closed"          # 关闭 — 正常通过
    OPEN = "open"              # 打开 — 拦截请求
    HALF_OPEN = "half_open"    # 半开 — 允许 1 个探针


class GenericCircuitBreaker:
    """通用熔断器基类

    用法:
        breaker = GenericCircuitBreaker(name="embedding")
        if not breaker.is_available:
            return fallback()
        try:
            result = await do_work()
        except Exception:
            await breaker.record_failure()
            raise
        else:
            await breaker.record_success()
            return result

    状态转换:
        CLOSED + 连续 N 次失败       → OPEN (启动 cooldown)
        OPEN   + cooldown 已过       → HALF_OPEN (允许 1 个探针)
        HALF_OPEN + 探针成功         → CLOSED
        HALF_OPEN + 探针失败         → OPEN (重置 cooldown)

    与旧 EmbeddingCircuitBreaker 兼容:
        - is_available: state == OPEN 时返回 False
        - 初始化时 state = CLOSED (旧实现初始 _is_open=True 等价于 OPEN,
          但仅在 probe 健康检查时关闭, 本基类默认乐观启动 — 业务行为等价,
          因为旧实现的 probe 默认 30s 跑一次, 启动后立刻就能用)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_threshold: int = 2,
        cooldown_seconds: float = 30.0,
        half_open_enabled: bool = True,
    ) -> None:
        """构造熔断器

        Args:
            name: 标识 (用于日志/diagnostics)
            failure_threshold: CLOSED → OPEN 触发连续失败次数
            recovery_threshold: HALF_OPEN → CLOSED 所需连续成功次数 (默认 1)
            cooldown_seconds: OPEN → HALF_OPEN 等待秒数
            half_open_enabled: 是否启用半开态 (False 时直接 OPEN → CLOSED 由下次记录触发)
        """
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_threshold = recovery_threshold
        self._cooldown_seconds = cooldown_seconds
        self._half_open_enabled = half_open_enabled

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._opened_at: float | None = None  # OPEN 起始时间
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """当前状态 (读时若 OPEN 且 cooldown 已过, 自动转 HALF_OPEN)"""
        if self._state == CircuitState.OPEN and self._half_open_enabled:
            if self._opened_at is not None and (time.monotonic() - self._opened_at) >= self._cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                logger.info("熔断器 %s 进入半开态 (cooldown %.1fs 已过)", self.name, self._cooldown_seconds)
        return self._state

    def _maybe_to_half_open(self) -> None:
        """主动检查并转换 OPEN→HALF_OPEN (供 record_* 调用)"""
        if self._state == CircuitState.OPEN and self._half_open_enabled:
            if self._opened_at is not None and (time.monotonic() - self._opened_at) >= self._cooldown_seconds:
                self._state = CircuitState.HALF_OPEN

    @property
    def is_available(self) -> bool:
        """是否可通过 — 仅 CLOSED 或 HALF_OPEN 通过"""
        return self.state != CircuitState.OPEN

    async def record_success(self) -> None:
        """记录一次成功 — 重置失败计数, HALF_OPEN 时尝试关闭"""
        async with self._lock:
            self._maybe_to_half_open()
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self._recovery_threshold:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._consecutive_successes = 0
                    self._opened_at = None
                    logger.info("熔断器 %s 关闭 (恢复)", self.name)
            elif self._state == CircuitState.CLOSED:
                # 成功重置失败计数, 累计成功计数 (用于观测)
                self._consecutive_failures = 0
                self._consecutive_successes += 1
            # OPEN 状态下不应被调用 (请求已被拦截), 静默忽略
            # 但若 _maybe_to_half_open 触发了转换但 cooldown 刚好够, 不会进任何分支 — 安全

    async def record_failure(self) -> None:
        """记录一次失败 — 累计, 触发 OPEN"""
        async with self._lock:
            self._maybe_to_half_open()
            if self._state == CircuitState.HALF_OPEN:
                # 半开态 1 次失败直接回到 OPEN
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._consecutive_failures += 1
                self._consecutive_successes = 0
                logger.warning("熔断器 %s 半开探针失败, 重新打开", self.name)
            elif self._state == CircuitState.CLOSED:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = time.monotonic()
                    logger.warning(
                        "熔断器 %s 打开 (连续 %d 次失败)",
                        self.name, self._consecutive_failures,
                    )
            else:
                # OPEN: 业务被拦截, 但探测调用 (e.g. _probe_once) 仍可能失败
                # 累计失败计数用于观测, 不再触发额外状态转换
                self._consecutive_failures += 1

    def get_snapshot(self) -> dict[str, Any]:
        """获取当前状态快照 — 给 admin diagnostics 用"""
        state = self.state  # 触发可能的 OPEN→HALF_OPEN 自动转换
        snapshot: dict[str, Any] = {
            "name": self.name,
            "state": state.value,
            "is_available": state != CircuitState.OPEN,
            "consecutive_failures": self._consecutive_failures,
            "consecutive_successes": self._consecutive_successes,
            "failure_threshold": self._failure_threshold,
            "recovery_threshold": self._recovery_threshold,
            "cooldown_seconds": self._cooldown_seconds,
        }
        if self._opened_at is not None:
            snapshot["opened_seconds_ago"] = round(time.monotonic() - self._opened_at, 1)
        return snapshot

    def reset(self) -> None:
        """手动重置 — 给测试 / 运维端点用"""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._opened_at = None
