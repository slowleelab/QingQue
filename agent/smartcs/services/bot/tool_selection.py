"""渐进式工具暴露（Progressive Disclosure）选择器

纯函数：根据意图 + 置信度 + 配置，决定向 LLM 暴露的工具子集。

设计要点（零回归）：
- 与网关的路由/聚合模式正交——本模块位于编排（host）层，只负责「暴露哪些工具」。
- 关闭开关（``progressive_disclosure_enabled=False``）时返回 ``None``，等价于暴露全量工具，
  行为与打通前完全一致。
- 命中意图且置信度达标 → 返回该意图的工具子集名单；否则（未命中/低置信）返回 ``None``，
  由 LLM 在全量工具上自行判断。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from smartcs.shared.models import IntentLabel

if TYPE_CHECKING:
    from smartcs.shared.config import MCPSettings

# 允许进入工具编排路径的「查询类工具意图」集合。
# 仅这些意图会尝试打通 MCP 工具；挂失/投诉/转人工仍直接转人工，闲聊/FAQ 仍走知识问答。
TOOL_INTENTS: frozenset[IntentLabel] = frozenset(
    {
        IntentLabel.BILL_QUERY,
        IntentLabel.TRANSACTION_QUERY,
        IntentLabel.LIMIT_QUERY,
        IntentLabel.INSTALLMENT_INQUIRY,
        IntentLabel.REWARD_QUERY,
    }
)


def select_tools_for_intent(
    intent: IntentLabel,
    confidence: float,
    settings: MCPSettings,
) -> list[str] | None:
    """根据意图与置信度选择要暴露的工具子集。

    :param intent: 主意图
    :param confidence: 主意图置信度
    :param settings: MCP 配置（含开关、阈值、意图→工具映射）
    :returns: 工具名子集；``None`` 表示暴露全量工具（不裁剪）
    """
    # 开关关闭 → 不裁剪，暴露全量（零回归）
    if not settings.progressive_disclosure_enabled:
        return None

    key = intent.value if isinstance(intent, IntentLabel) else str(intent)
    names = settings.intent_tool_map.get(key)
    # 未配置该意图或子集为空 → 暴露全量交 LLM 判断
    if not names:
        return None

    # 低置信 → 不裁剪，避免因误分类而漏掉必要工具
    if confidence < settings.pd_confidence_threshold:
        return None

    return list(names)
