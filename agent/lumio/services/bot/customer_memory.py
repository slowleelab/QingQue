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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lumio.shared.orm_models import DialogueLog

if TYPE_CHECKING:
    from lumio.services.common.session import SessionManager

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

    P1-3 上下文工程修复: 结果缓存 24h (Redis) — 旧实现每会话全量 SQL 聚合 90 天,
    高频客户每新会话全扫 + string_agg 千行文本, 且无复合索引支撑.

    Returns:
        {vip_level, card_types, risk_tolerance} 或空 dict
    """
    # 1. 缓存命中直接返回 (24h TTL, 画像属低频变化数据)
    try:
        from lumio.services.common.redis_client import get_redis_client

        redis = get_redis_client()
        cache_key = f"lumio:profile:cache:{customer_id}"
        cached = await redis.get(cache_key)
        if cached:
            import json as _json

            return _json.loads(cached)
    except Exception:
        pass  # Redis 不可用 → 直接计算

    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    profiles: dict[str, object] = {}

    try:
        async with session_factory() as session:
            # 聚合对话内容（取最近 90 天的 customer 发言, 限 1000 条防内存爆炸）
            result = await session.execute(
                select(func.string_agg(DialogueLog.content, "\n"))
                .where(
                    DialogueLog.customer_id == customer_id,
                    DialogueLog.speaker == "customer",
                    DialogueLog.created_at >= cutoff,
                    DialogueLog.id.in_(
                        select(DialogueLog.id)
                        .where(
                            DialogueLog.customer_id == customer_id,
                            DialogueLog.speaker == "customer",
                            DialogueLog.created_at >= cutoff,
                        )
                        .order_by(DialogueLog.created_at.desc())
                        .limit(1000)
                    ),
                )
            )
            all_content = result.scalar() or ""

        if not all_content:
            return profiles

        # 大小写不敏感 (regex 用 re.IGNORECASE, 此处 no-op 已删)
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
            if re.search(pattern, all_content, re.IGNORECASE) and score > best_score:
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

    # 2. 写缓存 (24h TTL; 空结果也缓存, 防高频空查询)
    try:
        from lumio.services.common.redis_client import get_redis_client

        redis = get_redis_client()
        import json as _json

        await redis.setex(
            f"lumio:profile:cache:{customer_id}",
            86400,
            _json.dumps(profiles, ensure_ascii=False),
        )
    except Exception:
        pass

    return profiles


async def apply_learned_profile(
    customer_id: str,
    session_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    session_manager: SessionManager,
) -> bool:
    """学习并应用客户画像到当前会话状态

    在 bot_agent.run() 开始时调用，首次为当前会话注入从历史学到的画像。
    使用 CAS patch 避免覆盖已在当前对话中更新的字段。

    D0 改造: 客户画像衰减 / 降级
    - 每个画像字段计算 effective_value = value * decay_factor(days_since_update)
    - 衰减曲线: decay = 0.95 ^ days_since_update (每天衰减 5%)
    - 跌破阈值自动降级 (VIP 钻石→金→银→普通)
    - 客户画像更新写入时间戳, 90 天后重新学习视为过期
    """
    import time as _time

    profiles = await learn_customer_profile(customer_id, session_factory)
    if not profiles:
        return False

    try:
        state = await session_manager.get_session(session_id)
        if state is None:
            return False

        # D0: 计算每个画像字段的 effective_value (考虑衰减)
        now = _time.time()
        vip_updated_at = getattr(state, "vip_level_updated_at", 0) or 0
        risk_updated_at = getattr(state, "risk_tolerance_updated_at", 0) or 0
        card_updated_at = getattr(state, "card_types_updated_at", 0) or 0

        vip_days = max(0, (now - vip_updated_at) / 86400) if vip_updated_at else 999
        risk_days = max(0, (now - risk_updated_at) / 86400) if risk_updated_at else 999
        card_days = max(0, (now - card_updated_at) / 86400) if card_updated_at else 999

        # 衰减系数
        vip_decay = 0.95 ** vip_days if vip_days < 999 else 0.0
        risk_decay = 0.95 ** risk_days if risk_days < 999 else 0.0
        card_decay = 0.95 ** card_days if card_days < 999 else 0.0

        # VIP 衰减: 跌破 0.5 → 降级
        vip_learned = profiles.get("vip_level")
        effective_vip = vip_learned
        if vip_learned and vip_decay < 0.5:
            effective_vip = _demote_vip(vip_learned)
            logger.info(
                "VIP 衰减降级: original=%s, decay=%.2f, effective=%s",
                vip_learned,
                vip_decay,
                effective_vip,
            )

        # 风险偏好衰减: 跌破 0.5 → 保守化
        risk_learned = profiles.get("risk_tolerance")
        effective_risk = risk_learned
        if risk_learned and risk_decay < 0.5:
            effective_risk = _conservative_risk(risk_learned)

        # 客户已显式声明的优先, 衰减画像不覆盖
        patches: dict[str, object] = {}
        if profiles.get("card_types") and not state.card_types and card_decay > 0.3:
            patches["card_types"] = profiles["card_types"]
            patches["card_types_updated_at"] = now
        if effective_vip and (not state.vip_level or state.vip_level == "普通"):
            patches["vip_level"] = effective_vip
            patches["vip_level_updated_at"] = now
        if effective_risk and (not state.risk_tolerance or state.risk_tolerance == "R2"):
            patches["risk_tolerance"] = effective_risk
            patches["risk_tolerance_updated_at"] = now

        if patches:
            await session_manager.patch_state(
                session_id=session_id,
                expected_version=state.version,
                patches=patches,
                writer="customer_memory:learn:decay",
            )
            logger.info(
                "客户画像已应用 (D0 衰减): session=%s customer=%s decay_vip=%.2f decay_risk=%.2f",
                session_id,
                customer_id,
                vip_decay,
                risk_decay,
            )
            return True
    except Exception as e:
        logger.debug("客户画像应用失败: session=%s error=%s", session_id, e)

    return False


def _demote_vip(vip: str) -> str:
    """VIP 降级路径: 钻石→金→银→普通."""
    demotion = {
        "钻石": "金",
        "金": "银",
        "银": "普通",
        "普通": "普通",
    }
    return demotion.get(vip, "普通")


def _conservative_risk(risk: str) -> str:
    """风险偏好保守化: R4→R3→R2→R1."""
    conservative = {
        "R4": "R3",  # 激进 → 偏高
        "R3": "R2",  # 偏高 → 中性
        "R2": "R1",  # 中性 → 保守
        "R1": "R1",
    }
    return conservative.get(risk, "R2")
