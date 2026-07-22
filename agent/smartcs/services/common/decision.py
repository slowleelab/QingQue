"""展示决策模块

替代硬编码的轮次冷却计数器（cooldown_remaining / suppress_flag），
基于场景 + 时间 + 坐席反馈的决策函数。

设计原则:
- 风控告警永不降级（BLOCK/WARN 级别强制展示）
- 紧急场景（挂失/盗刷/投诉）关闭营销
- 时间间隔替代轮次计数（避免快慢对话差异）
- 坐席反馈驱动动态调整（连续采纳缩短间隔，关闭延长间隔）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Scene(Enum):
    """会话场景枚举"""

    URGENT = "urgent"  # 挂失/盗刷/投诉
    INQUIRY = "inquiry"  # 查积分/查账单/查额度
    SALES = "sales"  # 办卡/提额/分期
    GENERAL = "general"  # 其他


class FeedbackAction(Enum):
    """坐席反馈动作"""

    ADOPTED = "adopted"  # 坐席采纳了建议
    DISMISSED = "dismissed"  # 坐席关闭了弹窗
    MODIFIED = "modified"  # 坐席修改后发送
    IGNORED = "ignored"  # 坐席未操作


# ── 场景快速检测（纯规则，0ms）──

# 紧急关键词（挂失、盗刷、投诉）
_URGENT_KEYWORDS = [
    "挂失",
    "丢了",
    "被盗",
    "盗刷",
    "投诉",
    "报警",
    "冻结",
    "锁卡",
    "停用",
    "异常交易",
    "不是我花的",
    "卡不见了",
    "卡没了",
    "卡被偷",
    "卡丢了",
]

# 销售场景关键词（办卡、提额、分期）
_SALES_KEYWORDS = [
    "办卡",
    "申请",
    "额度",
    "提额",
    "分期",
    "年费",
    "权益",
    "积分兑换",
    "升级",
    "白金",
]

# 查询场景关键词
_INQUIRY_KEYWORDS = [
    "账单",
    "消费",
    "积分",
    "余额",
    "还款",
    "明细",
    "记录",
    "查询",
    "还有多少",
]


def detect_scene(message: str) -> Scene:
    """基于关键词的场景快速检测（纯规则引擎，不调 LLM）

    Args:
        message: 客户消息文本

    Returns:
        检测到的场景枚举
    """
    text = message.strip().lower()

    for kw in _URGENT_KEYWORDS:
        if kw in text:
            return Scene.URGENT

    for kw in _SALES_KEYWORDS:
        if kw in text:
            return Scene.SALES

    for kw in _INQUIRY_KEYWORDS:
        if kw in text:
            return Scene.INQUIRY

    return Scene.GENERAL


# ── 展示决策 ──


@dataclass
class ShowDecision:
    """展示决策结果"""

    should_show: bool
    reason: str


@dataclass
class PushTracker:
    """推送追踪器（per-session 状态）

    存储最近推送时间和反馈历史，供展示决策使用。
    """

    # 各类型上次推送时间戳 (monotonic seconds)
    last_push_at: dict[str, float] = field(default_factory=dict)

    # 各类型最近反馈历史: [(action, timestamp), ...]
    feedback_history: dict[str, list[tuple[str, float]]] = field(default_factory=dict)

    # 各类型当前最小展示间隔（秒），反馈动态调整
    min_interval: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from smartcs.shared.config import get_settings

        cfg = get_settings().orchestration
        if not self.min_interval:
            self.min_interval = {
                "ai": cfg.base_interval_ai,
                "marketing": cfg.base_interval_marketing,
            }

    def record_push(self, card_type: str) -> None:
        """记录推送时间（使用 Unix 时间戳，跨实例一致）"""
        self.last_push_at[card_type] = time.time()

    def record_feedback(self, card_type: str, action: FeedbackAction) -> None:
        """记录坐席反馈并动态调整间隔"""
        from smartcs.shared.config import get_settings

        cfg = get_settings().orchestration
        history = self.feedback_history.setdefault(card_type, [])
        history.append((action.value, time.time()))
        # 只保留最近 10 条
        if len(history) > 10:
            history.pop(0)

        # 动态调整最小间隔
        base = self.min_interval.get(card_type, 3.0)

        if action == FeedbackAction.ADOPTED:
            # 连续采纳 3 次 → 缩短间隔
            recent = [a for a, _ in history[-3:]]
            if len(recent) >= 3 and all(a == FeedbackAction.ADOPTED.value for a in recent):
                self.min_interval[card_type] = max(1.0, base * cfg.adoption_shorten_ratio)
        elif action == FeedbackAction.DISMISSED:
            # 坐席关闭 → 延长间隔
            self.min_interval[card_type] = min(120.0, base * cfg.dismiss_extend_ratio)
        elif action == FeedbackAction.IGNORED:
            # 坐席忽略 → 微调延长
            recent = [a for a, _ in history[-5:]]
            ignored_count = sum(1 for a in recent if a == FeedbackAction.IGNORED.value)
            if ignored_count >= 3:
                self.min_interval[card_type] = min(60.0, base * cfg.ignore_extend_ratio)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 Redis 可存储的字典"""
        return {
            "last_push_at": self.last_push_at,
            "feedback_history": self.feedback_history,
            "min_interval": self.min_interval,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PushTracker:
        """从 Redis 字典反序列化"""
        if data is None:
            return cls()
        return cls(
            last_push_at=data.get("last_push_at", {}),
            feedback_history=data.get("feedback_history", {}),
            min_interval=data.get(
                "min_interval",
                {
                    "ai": 3.0,
                    "marketing": 30.0,
                },
            ),
        )


def should_show(
    card_type: str,
    scene: Scene,
    tracker: PushTracker,
    risk_action: str = "PASS",
    force_show: bool = False,
) -> ShowDecision:
    """判断某类卡片是否应该展示

    Args:
        card_type: 卡片类型 ("ai", "marketing", "risk")
        scene: 当前会话场景
        tracker: 推送追踪器（per-session 状态）
        risk_action: 风控动作 (PASS/WARN/BLOCK)，仅 card_type="risk" 时使用
        force_show: 是否强制展示（用于紧急告警等不可降级的场景）

    Returns:
        展示决策
    """
    # ── 硬规则：风控告警永不降级 ──
    if card_type == "risk":
        if risk_action in ("BLOCK", "WARN"):
            return ShowDecision(should_show=True, reason="风控告警强制展示")
        # PASS 且无告警内容 → 不展示
        return ShowDecision(should_show=False, reason="风控放行无需展示")

    # ── 硬规则：强制展示 ──
    if force_show:
        return ShowDecision(should_show=True, reason="强制展示")

    # ── 场景规则：紧急场景关闭营销 ──
    if card_type == "marketing" and scene == Scene.URGENT:
        return ShowDecision(should_show=False, reason="紧急场景关闭营销")

    # ── 时间规则：距上次推送不足最小间隔 ──
    min_interval = tracker.min_interval.get(card_type, 3.0)
    last_push = tracker.last_push_at.get(card_type, 0.0)
    elapsed = time.time() - last_push
    if elapsed < min_interval:
        return ShowDecision(
            should_show=False,
            reason=f"距上次推送仅{elapsed:.1f}s < 最小间隔{min_interval:.0f}s",
        )

    # ── 反馈规则：上次被关闭 → 本轮不推 ──
    history = tracker.feedback_history.get(card_type, [])
    if history:
        last_action, _ = history[-1]
        if last_action == FeedbackAction.DISMISSED.value:
            return ShowDecision(should_show=False, reason="上次推送被坐席关闭")

    # ── 所有检查通过 ──
    return ShowDecision(should_show=True, reason="通过所有检查")
