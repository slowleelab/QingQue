"""话术模板管理与检索"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from smartcs.shared.models import IntentLabel

logger = logging.getLogger(__name__)

# ── 种子话术数据 ──

_SEED_SCRIPTS: list[dict[str, Any]] = [
    # FAQ
    {
        "script_id": "S-FAQ-001",
        "category": "faq",
        "tags": ["年费", "减免"],
        "title": "年费政策说明",
        "content": "{customer_name}您好，我行信用卡年费政策为：普卡首年免年费，刷卡6次免次年。您可通过手机银行查询具体年费信息。",
        "variables": ["customer_name"],
        "priority": 8,
        "card_types": [],
        "customer_tiers": [],
    },
    {
        "script_id": "S-FAQ-002",
        "category": "faq",
        "tags": ["积分", "兑换"],
        "title": "积分兑换说明",
        "content": "您的积分可在「信用卡APP-我的积分」中兑换礼品或抵扣年费，当前兑换比例为{points_ratio}。",
        "variables": ["points_ratio"],
        "priority": 7,
        "card_types": [],
        "customer_tiers": [],
    },
    # bill_query
    {
        "script_id": "S-BILL-001",
        "category": "bill_query",
        "tags": ["账单", "还款"],
        "title": "账单查询回复",
        "content": "{customer_name}您好，您本期账单金额为{bill_amount}元，到期还款日为{due_date}，请及时还款。",
        "variables": ["customer_name", "bill_amount", "due_date"],
        "priority": 9,
        "card_types": [],
        "customer_tiers": [],
    },
    {
        "script_id": "S-BILL-002",
        "category": "bill_query",
        "tags": ["最低还款"],
        "title": "最低还款说明",
        "content": "您本期最低还款额为{min_amount}元。温馨提示：选择最低还款将产生利息，建议全额还款。",
        "variables": ["min_amount"],
        "priority": 8,
        "card_types": [],
        "customer_tiers": [],
    },
    # installment_inquiry
    {
        "script_id": "S-INST-001",
        "category": "installment_inquiry",
        "tags": ["分期", "手续费"],
        "title": "分期方案介绍",
        "content": "您的{bill_amount}元账单可分{tenor_options}期，每期手续费率约{rate}%。目前我行有分期优惠活动，具体以页面显示为准。",
        "variables": ["bill_amount", "tenor_options", "rate"],
        "priority": 9,
        "card_types": [],
        "customer_tiers": [],
    },
    # limit_query
    {
        "script_id": "S-LIMIT-001",
        "category": "limit_query",
        "tags": ["额度", "查询"],
        "title": "额度查询回复",
        "content": "您当前信用额度为{credit_limit}元，可用额度为{available_limit}元。如需提额，可在APP提交申请。",
        "variables": ["credit_limit", "available_limit"],
        "priority": 8,
        "card_types": [],
        "customer_tiers": [],
    },
    # card_loss
    {
        "script_id": "S-LOSS-001",
        "category": "card_loss",
        "tags": ["挂失", "紧急"],
        "title": "挂失引导",
        "content": "已为您锁定卡片，请确认以下信息：最后交易时间{last_txn_time}，交易金额{last_txn_amount}元是否为本人操作？",
        "variables": ["last_txn_time", "last_txn_amount"],
        "priority": 10,
        "card_types": [],
        "customer_tiers": [],
    },
    # complaint
    {
        "script_id": "S-COMP-001",
        "category": "complaint",
        "tags": ["投诉", "安抚"],
        "title": "投诉安抚",
        "content": "非常抱歉给您带来不好的体验。我已记录您反馈的问题，会加急处理并在24小时内回复您。",
        "variables": [],
        "priority": 10,
        "card_types": [],
        "customer_tiers": [],
    },
    # chitchat
    {
        "script_id": "S-CHAT-001",
        "category": "chitchat",
        "tags": ["问候", "开场"],
        "title": "标准开场",
        "content": "您好，我是您的客户经理，很高兴为您服务。请问有什么可以帮您的？",
        "variables": [],
        "priority": 5,
        "card_types": [],
        "customer_tiers": [],
    },
    {
        "script_id": "S-CHAT-002",
        "category": "chitchat",
        "tags": ["结束", "告别"],
        "title": "标准结束语",
        "content": "感谢您的来电，如有其他问题随时联系我们。祝您生活愉快！",
        "variables": [],
        "priority": 5,
        "card_types": [],
        "customer_tiers": [],
    },
    # 通用FAQ
    {
        "script_id": "S-FAQ-003",
        "category": "faq",
        "tags": ["安全", "盗刷"],
        "title": "安全提示",
        "content": "如发现异常交易请立即联系我行客服挂失。挂失前48小时内非本人交易可申请赔付。",
        "variables": [],
        "priority": 7,
        "card_types": [],
        "customer_tiers": [],
    },
    {
        "script_id": "S-FAQ-004",
        "category": "faq",
        "tags": ["手续费", "取现"],
        "title": "取现手续费说明",
        "content": "信用卡取现手续费为取现金额的{cash_advance_fee_rate}%，最低{cash_advance_min_fee}元/笔，并按日计息。",
        "variables": ["cash_advance_fee_rate", "cash_advance_min_fee"],
        "priority": 6,
        "card_types": [],
        "customer_tiers": [],
    },
]


class ScriptService:
    """话术服务

    支持内存加载和 PostgreSQL 加载两种模式。
    检索策略：意图匹配 → 优先级排序 → Top-K。
    """

    def __init__(self) -> None:
        self._scripts: list[dict[str, Any]] = []
        self._category_index: dict[str, list[int]] = {}  # category → indices in _scripts
        self._loaded_at: float = 0.0

    def load_from_memory(self, scripts: list[dict[str, Any]] | None = None) -> None:
        """从内存加载话术（开发/种子数据）"""
        self._scripts = list(scripts or _SEED_SCRIPTS)
        self._build_index()
        self._loaded_at = time.time()
        logger.info("从内存加载 %d 条话术模板", len(self._scripts))

    async def load_from_db(self, db_session) -> None:
        """从数据库加载 ACTIVE 话术（生产模式）"""
        from sqlalchemy import select

        from smartcs.shared.orm_models import ScriptStatus, ScriptTemplate

        result = await db_session.execute(select(ScriptTemplate).where(ScriptTemplate.status == ScriptStatus.ACTIVE))
        rows = result.scalars().all()
        self._scripts = [
            {
                "script_id": r.script_id,
                "category": r.category,
                "tags": r.tags,
                "title": r.title,
                "content": r.content,
                "variables": r.variables,
                "priority": r.priority,
                "card_types": [],
                "customer_tiers": [],
            }
            for r in rows
        ]
        self._build_index()
        self._loaded_at = time.time()
        logger.info("从数据库加载 %d 条话术模板", len(self._scripts))

    def _build_index(self) -> None:
        self._category_index.clear()
        for i, script in enumerate(self._scripts):
            cat = script["category"]
            self._category_index.setdefault(cat, []).append(i)

    def retrieve(
        self,
        intent: IntentLabel,
        top_k: int = 3,
        customer_tier: str | None = None,
        card_type: str | None = None,
        keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按意图检索话术，按优先级降序返回 top_k"""
        indices = self._category_index.get(intent.value, [])
        if not indices:
            return []
        candidates = [self._scripts[i] for i in indices]

        # 优先级降序
        candidates.sort(key=lambda s: s["priority"], reverse=True)

        # 若有卡片类型过滤（可选）
        if card_type:
            filtered = [s for s in candidates if not s["card_types"] or card_type in s["card_types"]]
            if filtered:
                candidates = filtered

        # 若有客户等级过滤（可选）
        if customer_tier:
            filtered = [s for s in candidates if not s["customer_tiers"] or customer_tier in s["customer_tiers"]]
            if filtered:
                candidates = filtered

        return candidates[:top_k]

    def resolve_variables(
        self,
        script: dict[str, Any],
        variables: dict[str, str],
    ) -> str:
        """解析话术模板变量，填充占位符"""
        content = script["content"]
        for var_name in script.get("variables", []):
            value = variables.get(var_name, f"{{{var_name}}}")
            content = content.replace(f"{{{var_name}}}", str(value))
        return content

    async def polish(
        self,
        script_content: str,
        context: str,
        llm_client,
        timeout_ms: int = 300,
    ) -> str:
        """LLM 润色话术（超时则返回原文）"""
        try:
            system_prompt = (
                "你是银行信用卡客户经理的话术润色助手。请根据上下文调整话术，使其更自然亲切。"
                "规则：1) 保持原意 2) 语气亲切不做作 3) 不添加未经确认的信息 4) 直接输出润色后文本，不解释。"
            )
            user_prompt = f"对话上下文：\n{context}\n\n话术模板：\n{script_content}\n\n请润色："
            response = await asyncio.wait_for(
                llm_client.chat(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    max_tokens=256,
                ),
                timeout=timeout_ms / 1000,
            )
            return response.strip() or script_content
        except TimeoutError:
            logger.warning("LLM 润色超时，返回原文")
            return script_content
        except Exception as e:
            logger.warning("LLM 润色失败: %s，返回原文", e)
            return script_content

    @property
    def loaded_at(self) -> float:
        return self._loaded_at
