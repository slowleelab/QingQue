"""跨会话客户记忆服务

从 PostgreSQL dialogue_log 表中学习客户画像，持久化到 SessionState。
解决"回头客的 VIP 等级、卡种、风险偏好永远是默认值"的问题。

策略:
- 聚合历史对话中的显式信号（如"我是白金卡""我要投诉银保监"）
- 每日增量更新，避免每轮对话都查全表
- 画像字段写入 SessionState.{vip_level, card_types, risk_tolerance}
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smartcs.shared.orm_models import DialogueLog

logger = logging.getLogger(__name__)

# 卡种关键词 → 正式名称
_CARD_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"白金卡|白金", "platinum"),
    (r"钻石卡|钻石|无限卡", "diamond"),
    (r"金卡|gold", "gold"),
    (r"普卡|标准卡", "standard"),
]

# VIP 等级信号（显式声明或隐含推论）
_VIP_SIGNALS: list[tuple[str, str, int]] = [
    (r"私银|私人银行|private.?banking", "private_banking", 5),
    (r"财富管理|贵宾", "wealth_management", 4),
    (r"vip|白金|尊享|专属", "vip", 3),
]

# 风险偏好信号（值越大越激进）
_RISK_SIGNALS: list[tuple[str, int]] = [
    (r"分期|借钱|贷款|融资", 1),       # 倾向借款 → 风险略高
    (r"理财|投资|基金|股票|收益", 3),   # 主动投资 → 高风险偏好
    (r"不敢.*分期|怕.*逾期|保守|稳健", -2),  # 厌恶风险
]


async def learn_customer_profile(
    customer_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    lookback_days: int = 90,
) -> dict[str, object]:
    """从历史对话中学习客户画像

    Returns:
        {vip_level, card_types, risk_tolerance} 或空 dict
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    profiles: dict[str, object] = {}

    try:
        async with session_factory() as session:
            # 聚合对话内容（取最近 90 天的 customer 发言）
            result = await session.execute(
                select(func.string_agg(DialogueLog.content, "\n"))
                .where(
                    DialogueLog.customer_id == customer_id,
                    DialogueLog.speaker == "customer",
                    DialogueLog.created_at >= cutoff,
                )
            )
            all_content = result.scalar() or ""

        if not all_content:
            return profiles

        all_lower = all_content.lower()

        # ── 卡种推断 ──
        card_types: list[str] = []
        for pattern, name in _CARD_TYPE_PATTERNS:
            if re.search(pattern, all_content, re.IGNORECASE):
                card_types.append(name)
        if card_types:
            profiles["card_types"] = card_types

        # ── VIP 等级推断（取最高分）──
        best_vip = "普通"
        best_score = 0
        for pattern, level, score in _VIP_SIGNALS:
            if re.search(pattern, all_content, re.IGNORECASE):
                if score > best_score:
                    best_score = score
                    best_vip = level
        if best_vip != "普通":
            profiles["vip_level"] = best_vip

        # ── 风险偏好推断 ──
        total_risk = 0
        for pattern, score in _RISK_SIGNALS:
            if re.search(pattern, all_content, re.IGNORECASE):
                total_risk += score
        if total_risk > 2:
            profiles["risk_tolerance"] = "R4"  # 激进
        elif total_risk > 0:
            profiles["risk_tolerance"] = "R3"  # 偏高
        elif total_risk < -1:
            profiles["risk_tolerance"] = "R1"  # 保守
        elif total_risk < 0:
            profiles["risk_tolerance"] = "R2"  # 中性偏保守
        # 0 → 保持默认 R2，不写入

        if profiles:
            logger.debug(
                "客户画像学习: customer=%s cards=%s vip=%s risk=%s",
                customer_id,
                profiles.get("card_types"),
                profiles.get("vip_level"),
                profiles.get("risk_tolerance"),
            )

    except Exception as e:
        logger.warning("客户画像学习失败: customer=%s error=%s", customer_id, e)

    return profiles


async def apply_learned_profile(
    customer_id: str,
    session_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    session_manager: object,
) -> bool:
    """学习并应用客户画像到当前会话状态

    在 bot_agent.run() 开始时调用，首次为当前会话注入从历史学到的画像。
    使用 CAS patch 避免覆盖已在当前对话中更新的字段。
    """
    profiles = await learn_customer_profile(customer_id, session_factory)
    if not profiles:
        return False

    try:
        state = await session_manager.get_session(session_id)
        if state is None:
            return False

        # 仅写入尚未在当前会话中设置的字段（已显式声明的优先）
        patches: dict[str, object] = {}
        if "card_types" in profiles and not state.card_types:
            patches["card_types"] = profiles["card_types"]
        if "vip_level" in profiles and (not state.vip_level or state.vip_level == "普通"):
            patches["vip_level"] = profiles["vip_level"]
        if "risk_tolerance" in profiles and (not state.risk_tolerance or state.risk_tolerance == "R2"):
            patches["risk_tolerance"] = profiles["risk_tolerance"]

        if patches:
            await session_manager.patch_state(
                session_id=session_id,
                expected_version=state.version,
                patches=patches,
                writer="customer_memory:learn",
            )
            logger.info("客户画像已应用: session=%s customer=%s", session_id, customer_id)
            return True
    except Exception as e:
        logger.debug("客户画像应用失败: session=%s error=%s", session_id, e)

    return False
