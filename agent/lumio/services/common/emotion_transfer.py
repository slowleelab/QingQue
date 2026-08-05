"""Sprint D: 情绪自动转人工.

alert_engine.py 愤怒告警 → 触发 TransferService.request(L2_semantic, reason="negative_emotion")
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from lumio.shared.logger import get_logger

logger = get_logger(__name__)


class EmotionTrigger(Enum):
    """情绪触发的转人工原因."""

    NEGATIVE_EMOTION = "negative_emotion"  # 负面情绪
    ANGRY = "angry"  # 愤怒
    ANXIOUS = "anxious"  # 焦虑
    DESPERATE = "desperate"  # 绝望 (投诉升级)


def should_transfer_by_emotion(sentiment: str, confidence: float = 0.8) -> tuple[bool, str]:
    """根据情绪判断是否触发转人工.

    规则:
    - angry + confidence > 0.7 → 转人工
    - negative + confidence > 0.85 → 转人工
    - desperate → 立即转人工 (兜底)
    """
    if sentiment == "DESPERATE":
        return True, EmotionTrigger.DESPERATE.value

    if sentiment == "ANGRY" and confidence > 0.7:
        return True, EmotionTrigger.ANGRY.value

    if sentiment == "NEGATIVE" and confidence > 0.85:
        return True, EmotionTrigger.NEGATIVE_EMOTION.value

    return False, ""


async def maybe_transfer_by_emotion(
    session_id: str,
    sentiment: str,
    confidence: float,
    transfer_service: Any = None,
) -> bool:
    """情绪触发转人工 (异步执行).

    Returns:
        是否触发
    """
    should, reason = should_transfer_by_emotion(sentiment, confidence)
    if not should:
        return False

    logger.info("情绪触发转人工: session=%s sentiment=%s reason=%s", session_id, sentiment, reason)

    # 实际转人工 (需要 transfer_service 实例, 此处占位)
    if transfer_service:
        try:
            await transfer_service.request(
                level="L2_semantic",
                reason=f"emotion:{reason}",
                session_id=session_id,
            )
        except Exception as exc:
            logger.error("情绪转人工执行失败: %s", exc)

    return True
