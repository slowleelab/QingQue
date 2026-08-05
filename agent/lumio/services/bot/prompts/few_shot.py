"""B1: Prompt 调优方法论 — Few-shot 动态选择.

核心思想:
- 维护 Few-shot 案例库 (按意图分类)
- LLM 调用时, 根据当前意图动态选 top-K 最相似案例
- 案例相似度用 embedding 余弦相似度
- 银行客服: 默认 Few-shot + ReAct, 复杂问题启用 CoT

优势:
- 减少幻觉: 给出参考案例, LLM 倾向按格式回答
- 改善一致性: 相似问题得到相似答复
- 持续学习: 差评案例 → 候选池 → 人工标注 → 扩 Few-shot
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from lumio.shared.logger import get_logger

logger = get_logger(__name__)


class CoTIntent(str, Enum):
    """需要启用 CoT (Chain of Thought) 的意图集.

    P2-4: 之前用凭空捏造的字符串 "BILL_INSTALLMENT" 等, 与 IntentLabel 枚举不一致.
    现统一改为按 IntentLabel 的实际值映射.
    """

    # 分期方案比较 — CoT 有助理清金额/期数/手续费
    INSTALLMENT_INQUIRY = "installment_inquiry"
    # 额度查询 — 涉及多卡合并/可用/总额
    LIMIT_QUERY = "limit_query"
    # 账单查询 — 涉及多笔/分期/还款
    BILL_QUERY = "bill_query"
    # 投诉 — 需要逐步推理客户诉求 + 同理心
    COMPLAINT = "complaint"


# 兼容别名: 允许老代码按字符串传入
_COT_ALIASES: dict[str, CoTIntent] = {
    "BILL_INSTALLMENT": CoTIntent.INSTALLMENT_INQUIRY,
    "CREDIT_LIMIT_ADJUST": CoTIntent.LIMIT_QUERY,
    "INTEREST_FREE_PERIOD": CoTIntent.BILL_QUERY,
    "OVERDUE_CONSEQUENCES": CoTIntent.BILL_QUERY,
}


# Few-shot 案例库 (按 intent 分组)
# 实际生产应该用 embedding 检索, 此处先用关键词匹配简化
FEW_SHOT_LIBRARY: dict[str, list[dict[str, str]]] = {
    "installment_inquiry": [  # R3: 键统一为 IntentLabel 值 (原 BILL_INSTALLMENT 与枚举不匹配)
        {
            "question": "我想分 6 期还账单",
            "answer": "账单分期支持 3 期/6 期/12 期/24 期, 6 期手续费率 0.75%/月. 您要办理吗?",
        },
        {
            "question": "账单分期可以提前还款吗",
            "answer": "可以提前还款, 已收取的手续费不退还, 剩余未分摊金额一次性入账.",
        },
    ],
    "limit_query": [
        {
            "question": "我想提高信用卡额度",
            "answer": "您可通过以下方式申请调额: 1) 手机银行 APP 提交 2) 拨打客服热线. 审批一般 3-5 个工作日.",
        },
        {
            "question": "调额最高能调多少",
            "answer": "调额上限根据您的用卡情况/收入/征信综合评估, 一般不超过当前额度的 2-3 倍.",
        },
    ],
    "card_loss": [
        {
            "question": "我的信用卡丢了",
            "answer": "请立即挂失! 我现在帮您转接人工客服进行口头挂失, 之后可申请补卡.",
        },
    ],
    "reward_query": [
        {
            "question": "我有多少积分",
            "answer": "您的积分可通过手机银行 APP / 信用卡官网 / 微信小程序查询, 兑换商品在积分商城.",
        },
    ],
    "complaint": [
        {
            "question": "我对你们的服务非常不满",
            "answer": "非常抱歉给您带来不愉快的体验, 我会立即为您记录反馈, 专员将在 1-3 个工作日内联系您.",
        },
    ],
    "bill_query": [
        {
            "question": "我这个月账单多少",
            "answer": "您的本期账单金额及还款日可通过手机银行 APP 查询, 我也可以为您简要说明账单构成.",
        },
    ],
    "faq": [
        {
            "question": "信用卡年费怎么免",
            "answer": "多数卡种每年消费满 6 次即免年费, 具体以卡种权益为准.",
        },
    ],
}

# R3 第三轮修复: 旧捏造键 → IntentLabel 值别名 (兼容历史调用)
_FEW_SHOT_KEY_ALIASES: dict[str, str] = {
    "BILL_INSTALLMENT": "installment_inquiry",
    "CREDIT_LIMIT_ADJUST": "limit_query",
    "CARD_LOSS": "card_loss",
    "POINTS_INQUIRY": "reward_query",
    "COMPLAINT": "complaint",
    "BILL_INQUIRY": "bill_query",
    "CASH_ADVANCE": "transaction_query",
}


# 关键词匹配映射 (intent → 关键词列表)
_INTENT_KEYWORDS: dict[str, list[str]] = {
    "installment_inquiry": ["分期", "账单", "手续费", "提前还款"],
    "limit_query": ["调额", "额度", "提高", "申请"],
    "card_loss": ["丢", "挂失", "补卡"],
    "reward_query": ["积分", "兑换", "商城"],
    "complaint": ["投诉", "不满", "服务差", "乱扣", "差评"],
    "bill_query": ["账单", "金额", "到期", "还款日"],
    "transaction_query": ["取现", "预借", "现金"],
    "faq": ["年费", "怎么办", "怎么用", "如何"],
    "transfer_agent": ["转人工", "人工", "客服电话"],
}


def select_few_shot(
    intent: str,
    user_input: str,
    top_k: int = 3,
) -> list[dict[str, str]]:
    """根据意图选 Few-shot 案例.

    简化版: 直接按 intent 分类返回 top_k 案例.
    生产版: 用 embedding 余弦相似度选最相似 top_k 案例.

    Args:
        intent: 当前意图 (IntentLabel 值, e.g. "installment_inquiry"; 兼容旧捏造键)
        user_input: 用户输入 (备用: 当 intent 未识别时用关键词匹配)
        top_k: 返回案例数

    Returns:
        案例列表 [{question, answer}, ...]
    """
    # R3: 旧键归一化 (BILL_INSTALLMENT → installment_inquiry)
    normalized = _FEW_SHOT_KEY_ALIASES.get(intent, intent)

    # 1. 直接按 intent 查
    candidates = FEW_SHOT_LIBRARY.get(normalized, [])

    # 2. intent 未命中, 用关键词匹配
    if not candidates and user_input:
        for int_key, keywords in _INTENT_KEYWORDS.items():
            if any(kw in user_input for kw in keywords):
                candidates = FEW_SHOT_LIBRARY.get(int_key, [])
                if candidates:
                    logger.debug("Few-shot 关键词匹配: %s → %s", intent, int_key)
                    break

    # 3. 截取 top_k
    return candidates[:top_k]


def get_cot_trigger(intents: list[Any] | None = None) -> bool:
    """判断是否启用 CoT (Chain of Thought).

    规则: 复杂金融问题 (分期/额度/账单/投诉) 启用 CoT.

    P2-4: 参数类型从 str 改为 Any (兼容 IntentLabel enum / 旧别名字符串 / CoTIntent enum).
    之前 set 中的 "BILL_INSTALLMENT" 等字符串与 IntentLabel 实际值不匹配,
    现在统一映射到 CoTIntent, 旧字符串走 _COT_ALIASES 兼容.
    """
    if not intents:
        return False
    cot_values = {c.value for c in CoTIntent}
    for i in intents:
        # enum / StrEnum: 取 .value
        v = getattr(i, "value", i)
        # 字符串别名兼容
        if v in _COT_ALIASES:
            v = _COT_ALIASES[v].value
        if v in cot_values:
            return True
    return False
