"""产品目录与匹配推荐"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from smartcs.shared.models import IntentLabel

logger = logging.getLogger(__name__)


@dataclass
class Product:
    product_id: str
    product_name: str
    category: str  # "credit_card" / "loan" / "installment"
    intents: list[str]  # 关联意图
    description: str
    eligibility_keywords: list[str] = field(default_factory=list)
    risk_tip: str = ""
    script_template: str = ""  # 推荐话术


_SEED_PRODUCTS: list[Product] = [
    Product(
        product_id="P001",
        product_name="自由分期",
        category="installment",
        intents=["installment_inquiry", "bill_query"],
        description="账单分期付款，3/6/12/24期可选",
        eligibility_keywords=["分期", "还款压力", "大额消费"],
        risk_tip="分期会产生手续费，请确认客户了解费率。",
        script_template="我行提供{bill_amount}元账单分{tenor}期方案，每期仅需{monthly}元。",
    ),
    Product(
        product_id="P002",
        product_name="积分加速卡",
        category="credit_card",
        intents=["reward_query", "faq"],
        description="消费双倍积分，年费可积分抵扣",
        eligibility_keywords=["积分", "兑换", "里程"],
        risk_tip="年费 580 元，需确认客户年消费额是否达到免年费标准。",
        script_template="推荐我行积分加速卡，消费双倍积分，{annual_fee_info}。",
    ),
    Product(
        product_id="P003",
        product_name="临时额度提升",
        category="credit_card",
        intents=["limit_query"],
        description="临时调高信用额度，有效期 30 天",
        eligibility_keywords=["额度不够", "提额", "临时额度"],
        risk_tip="临时额度到期后自动恢复，超额部分需一次性还清。",
        script_template="您当前可申请临时额度提升至{new_limit}元，有效期30天。",
    ),
    Product(
        product_id="P004",
        product_name="分期贷",
        category="loan",
        intents=["installment_inquiry", "limit_query"],
        description="大额消费贷款，最高 30 万",
        eligibility_keywords=["贷款", "大额", "装修", "旅游"],
        risk_tip="需征信查询，利率因人而异，请告知客户以实际审批为准。",
        script_template="我行分期贷最高{max_amount}万，{rate_info}，最快当天到账。",
    ),
]


class ProductCatalog:
    """产品目录

    支持按意图匹配推荐产品。
    """

    def __init__(self, products: list[Product] | None = None) -> None:
        self.products = products or list(_SEED_PRODUCTS)

    def match(self, intent: IntentLabel, top_k: int = 2) -> list[Product]:
        """按意图匹配产品"""
        matched = [p for p in self.products if intent.value in p.intents]
        return matched[:top_k]

    def get(self, product_id: str) -> Product | None:
        for p in self.products:
            if p.product_id == product_id:
                return p
        return None

    async def load_from_db(self, db_session) -> int:
        """从数据库加载产品（替代/补充内存种子数据）"""
        from sqlalchemy import select

        from smartcs.shared.orm_models import KbProduct, ProductStatus

        result = await db_session.execute(select(KbProduct).where(KbProduct.status == ProductStatus.ACTIVE.value))
        rows = result.scalars().all()
        loaded = 0
        for row in rows:
            product = Product(
                product_id=str(row.id),
                product_name=row.product_name,
                category=row.category,
                intents=row.intents or [],
                description=row.description or "",
                eligibility_keywords=row.eligibility_keywords or [],
            )
            self.add_product(product)
            loaded += 1

        logger.info("从数据库加载了 %d 个产品", loaded)
        return loaded
