"""会话超时管理器（Redis ZSET 分布式方案）

使用 Redis Sorted Set 存储超时到期时间戳，后台轮询扫描到期会话。
支持多实例部署、进程重启恢复，解决原 asyncio.Task 方案的单点故障问题。

工作原理:
- start_guard: ZADD smartcs:session:timeouts {expire_ts} {session_id}
- 后台轮询: 每 5s 扫描 ZRANGEBYSCORE score <= now，触发超时回调
- cancel_guard: ZREM 移除

支持的超时类型:
- BOT 空闲超时 → ENDED
- 排队超时 → 回退 BOT (AG_QUEUED → BOT_ACTIVE)
- 振铃超时 → ENDED (AG_ASSIGNED → ENDED)
- 会话时长超时 → ENDED (AG_ACTIVE → ENDED)
- 话后小结超时 → ENDED (AG_REVIEWING → ENDED)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from redis.asyncio import Redis

from smartcs.shared.config import get_settings
from smartcs.shared.exceptions import InvalidTransitionError, SessionNotFoundError
from smartcs.shared.metrics import SESSION_TIMEOUTS
from smartcs.shared.models import SessionPhase, SessionSubPhase

if TYPE_CHECKING:
    from smartcs.services.common.session import SessionManager

logger = logging.getLogger(__name__)

# 超时回调类型: (session_id, sub_phase, reason) → None
TimeoutCallback = Callable[[str, SessionSubPhase, str], Awaitable[None]]

# Redis ZSET key
_TIMEOUT_ZSET_KEY = "smartcs:session:timeouts"

# 轮询间隔（秒）
_POLL_INTERVAL = 5.0


class SessionTimeoutManager:
    """会话超时管理器（Redis ZSET 分布式方案）

    超时任务存储在 Redis ZSET 中，score 为到期 Unix 时间戳。
    后台轮询任务扫描到期条目并触发回调，支持多实例竞争执行（ZREM 返回值做幂等）。
    """

    def __init__(
        self,
        session_manager: SessionManager,
        redis: Redis,
        on_timeout: TimeoutCallback | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._redis = redis
        self._on_timeout = on_timeout
        settings = get_settings()
        self._bot_idle_timeout = settings.session.bot_idle_timeout
        self._queue_timeout = settings.session.queue_timeout
        self._ringing_timeout = settings.session.ringing_timeout
        self._session_timeout = settings.session.session_timeout
        self._review_timeout = settings.session.review_timeout

        # session_id → (sub_phase, expire_ts) 内存缓存（减少 Redis 查询）
        self._guards: dict[str, tuple[SessionSubPhase, float]] = {}

        # 后台轮询任务
        self._poller_task: asyncio.Task | None = None

    def start_guard(self, session_id: str, sub_phase: SessionSubPhase) -> None:
        """为会话启动超时守卫

        取消旧守卫，再根据子阶段启动新任务（写入 Redis ZSET）。
        """
        self.cancel_guard(session_id)

        timeout = self._get_timeout(sub_phase)
        if timeout is None:
            return

        expire_ts = time.time() + timeout
        self._guards[session_id] = (sub_phase, expire_ts)

        # 异步写入 Redis ZSET（不阻塞调用方）
        asyncio.create_task(self._redis.zadd(_TIMEOUT_ZSET_KEY, {session_id: expire_ts}))

        logger.debug(
            "超时守卫已启动: session=%s sub=%s timeout=%ds expire=%s",
            session_id,
            sub_phase.value,
            timeout,
            expire_ts,
        )

    def cancel_guard(self, session_id: str) -> None:
        """取消会话的超时守卫"""
        self._guards.pop(session_id, None)
        asyncio.create_task(self._redis.zrem(_TIMEOUT_ZSET_KEY, session_id))

    async def start_poller(self) -> None:
        """启动后台轮询任务"""
        if self._poller_task is None or self._poller_task.done():
            self._poller_task = asyncio.create_task(self._poll_loop(), name="session-timeout-poller")
            logger.info("会话超时轮询器已启动 (interval=%.0fs)", _POLL_INTERVAL)

    async def stop_poller(self) -> None:
        """停止后台轮询任务"""
        if self._poller_task and not self._poller_task.done():
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
        self._poller_task = None

        # 清理所有内存缓存
        self._guards.clear()

    async def _poll_loop(self) -> None:
        """超时轮询主循环

        每 _POLL_INTERVAL 秒扫描 Redis ZSET 中到期的会话，
        通过 ZREM 原子移除保证多实例下只有一个实例处理。
        """
        while True:
            try:
                await asyncio.sleep(_POLL_INTERVAL)

                now = time.time()
                # 获取所有到期会话
                expired = await self._redis.zrangebyscore(
                    _TIMEOUT_ZSET_KEY,
                    0,
                    now,
                    start=0,
                    num=100,
                )

                for raw_session_id in expired:
                    session_id = raw_session_id if isinstance(raw_session_id, str) else raw_session_id.decode()

                    # ZREM 原子移除：多实例竞争下只有一个成功
                    removed = await self._redis.zrem(_TIMEOUT_ZSET_KEY, session_id)
                    if not removed:
                        continue  # 已被其他实例处理

                    # 从内存缓存获取子阶段（fallback: 未知则按 ENDED 处理）
                    guard_info = self._guards.pop(session_id, None)
                    if guard_info:
                        sub_phase = guard_info[0]
                    else:
                        sub_phase = SessionSubPhase.AG_ACTIVE  # 保守处理

                    await self._handle_timeout(session_id, sub_phase)

            except asyncio.CancelledError:
                logger.info("会话超时轮询器收到取消信号")
                raise
            except Exception:
                logger.exception("超时轮询异常，%ds 后重试", int(_POLL_INTERVAL))
                await asyncio.sleep(_POLL_INTERVAL)

    async def _handle_timeout(self, session_id: str, sub_phase: SessionSubPhase) -> None:
        """处理单个会话超时"""
        logger.warning("会话超时: session=%s sub=%s", session_id, sub_phase.value)
        SESSION_TIMEOUTS.labels(
            sub_phase=sub_phase.value,
            reason=f"{sub_phase.value}_timeout",
        ).inc()

        reason = f"{sub_phase.value}_timeout"

        try:
            if sub_phase == SessionSubPhase.AG_QUEUED:
                # 排队超时 → 回退 BOT
                reason = "queue_timeout"
                await self._session_manager.transition_phase(
                    session_id,
                    SessionPhase.BOT,
                    new_sub_phase=SessionSubPhase.BOT_ACTIVE,
                    reason=reason,
                )
                logger.info("排队超时回退 BOT: session=%s", session_id)
            elif sub_phase == SessionSubPhase.AG_REVIEWING:
                reason = "review_timeout"
                await self._session_manager.transition_phase(
                    session_id,
                    SessionPhase.ENDED,
                    reason=reason,
                )
            else:
                await self._session_manager.transition_phase(
                    session_id,
                    SessionPhase.ENDED,
                    reason=reason,
                )

            # 通知坐席 UI
            if self._on_timeout:
                try:
                    await self._on_timeout(session_id, sub_phase, reason)
                except Exception:
                    logger.debug("超时回调执行失败: session=%s", session_id)
        except (SessionNotFoundError, InvalidTransitionError) as e:
            logger.debug("超时状态转换跳过: session=%s error=%s", session_id, e)
        except Exception:
            logger.exception("超时状态转换失败: session=%s", session_id)

    def _get_timeout(self, sub_phase: SessionSubPhase) -> int | None:
        """根据子阶段获取超时秒数"""
        mapping: dict[SessionSubPhase, int] = {
            SessionSubPhase.BOT_ACTIVE: self._bot_idle_timeout,
            SessionSubPhase.AG_QUEUED: self._queue_timeout,
            SessionSubPhase.AG_ASSIGNED: self._ringing_timeout,
            SessionSubPhase.AG_ACTIVE: self._session_timeout,
            SessionSubPhase.AG_ON_HOLD: self._session_timeout,
            SessionSubPhase.AG_REVIEWING: self._review_timeout,
        }
        return mapping.get(sub_phase)

    @property
    def active_guards(self) -> int:
        """当前活跃的超时守卫数"""
        return len(self._guards)
