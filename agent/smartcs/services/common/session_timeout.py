"""会话超时管理器

对每个活跃会话启动异步超时守卫任务，超时后自动执行状态转换。
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
from typing import TYPE_CHECKING

from smartcs.shared.config import get_settings
from smartcs.shared.models import SessionPhase, SessionSubPhase

if TYPE_CHECKING:
    from smartcs.services.common.session import SessionManager

logger = logging.getLogger(__name__)


class SessionTimeoutManager:
    """会话超时管理器

    为每个会话的子阶段维护一个超时守卫任务。
    当子阶段变更时，取消旧任务并启动新任务。
    """

    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager
        settings = get_settings()
        self._bot_idle_timeout = settings.session.bot_idle_timeout
        self._queue_timeout = settings.session.queue_timeout
        self._ringing_timeout = settings.session.ringing_timeout
        self._session_timeout = settings.session.session_timeout
        self._review_timeout = settings.session.review_timeout

        # session_id → asyncio.Task
        self._guards: dict[str, asyncio.Task] = {}

    def start_guard(self, session_id: str, sub_phase: SessionSubPhase) -> None:
        """为会话启动超时守卫

        如果已有守卫任务则先取消，再根据子阶段启动新任务。
        """
        self.cancel_guard(session_id)

        timeout = self._get_timeout(sub_phase)
        if timeout is None:
            return

        task = asyncio.create_task(
            self._guard_loop(session_id, sub_phase, timeout),
            name=f"timeout-guard-{session_id}-{sub_phase.value}",
        )
        self._guards[session_id] = task
        task.add_done_callback(lambda t: self._guards.pop(session_id, None) if self._guards.get(session_id) is t else None)
        logger.debug("超时守卫已启动: session=%s sub=%s timeout=%ds", session_id, sub_phase.value, timeout)

    def cancel_guard(self, session_id: str) -> None:
        """取消会话的超时守卫"""
        task = self._guards.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    async def _guard_loop(self, session_id: str, sub_phase: SessionSubPhase, timeout: int) -> None:
        """超时守卫循环"""
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return

        # 超时触发
        logger.warning("会话超时: session=%s sub=%s timeout=%ds", session_id, sub_phase.value, timeout)

        try:
            if sub_phase == SessionSubPhase.AG_QUEUED:
                # 排队超时 → 回退 BOT
                await self._session_manager.transition_phase(
                    session_id,
                    SessionPhase.BOT,
                    new_sub_phase=SessionSubPhase.BOT_ACTIVE,
                    reason="queue_timeout",
                )
                logger.info("排队超时回退 BOT: session=%s", session_id)
            elif sub_phase == SessionSubPhase.AG_REVIEWING:
                # 话后小结超时 → ENDED
                await self._session_manager.transition_phase(
                    session_id,
                    SessionPhase.ENDED,
                    reason="review_timeout",
                )
            else:
                # 其他超时 → ENDED
                await self._session_manager.transition_phase(
                    session_id,
                    SessionPhase.ENDED,
                    reason=f"{sub_phase.value}_timeout",
                )
        except ValueError as e:
            # 状态已被其他流程改变，静默处理
            logger.debug("超时状态转换跳过: session=%s error=%s", session_id, e)
        except Exception:
            logger.exception("超时状态转换失败: session=%s", session_id)

    def _get_timeout(self, sub_phase: SessionSubPhase) -> int | None:
        """根据子阶段获取超时秒数，None 表示不限时"""
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
