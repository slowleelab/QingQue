# Sprint 5: 坐席辅助 — 话术推荐 + 知识推送

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现生产级坐席辅助服务：并行分发话术推荐、知识推送、质检告警、产品推荐，WebSocket 实时推送到前端。

**Architecture:** WebSocket 接收客户消息 → 预处理（意图+情绪+实体）→ `asyncio.TaskGroup` 四路并行分发（话术/知识/质检/产品，各独立超时+降级）→ 汇聚 AssembPushPayload → 节流检查 → WebSocket 推送。话术模板存 PostgreSQL，Redis 缓存+热加载。

**Tech Stack:** FastAPI WebSocket, asyncio.TaskGroup, SQLAlchemy 2.0 async, Redis Pub/Sub, Pydantic

---

## File Structure Map

```
src/smartcs/
  shared/
    config.py              # +AssistSettings
    orm_models.py          # +ScriptTemplate, ScriptUsageLog, AlertRule, AlertLog
  services/assist/
    script_service.py      # 话术加载/检索/润色/变量解析/热加载
    alert_engine.py        # 合规+情绪+趋势+告警聚合
    product_catalog.py     # 产品目录+匹配推荐
    agent.py               # AssistOrchestrator (asyncio 并行编排)
    router.py              # 重写 WebSocket
  services/common/
    deps.py                # +init_assist_*, type aliases
  main.py                  # 补充 assist lifespan

alembic/versions/          # 新迁移(由 ORM 生成)

scripts/
  seed_scripts.py          # 种子话术+质检规则+产品

tests/
  test_script_service.py
  test_alert_engine.py
  test_product_catalog.py
  test_assist_agent.py
  test_assist_ws.py
```

---

### Task 1: AssistSettings 配置

**Files:**
- Modify: `src/smartcs/shared/config.py` (add class after existing settings)

- [ ] **Step 1: Add AssistSettings class**

Add this class after the existing `SessionSettings` class (search for `class SessionSettings`):

```python
class AssistSettings(BaseSettings):
    """坐席辅助配置"""

    model_config = SettingsConfigDict(env_prefix="ASSIST_")

    # 分支超时（毫秒）
    script_timeout_ms: int = 500
    knowledge_timeout_ms: int = 600
    alert_timeout_ms: int = 300
    product_timeout_ms: int = 400

    # 推送节流
    throttle_window_ms: int = 800

    # 话术
    polish_model: str = "qwen2.5:7b"
    script_cache_ttl: int = 300  # Redis 缓存秒数
    max_scripts_per_push: int = 3

    # 知识
    max_knowledge_per_push: int = 3

    # 情绪趋势
    sentiment_trend_window: int = 3  # 连续负面轮数触发升级

    # 产品
    max_recommendations_per_push: int = 2
```

Then add it to the `Settings` class (the composite class at the bottom of the file):

```python
assist: AssistSettings = Field(default_factory=AssistSettings)
```

- [ ] **Step 2: Verify**

```bash
poetry run python -c "from smartcs.shared.config import get_settings; s = get_settings(); print(s.assist.script_timeout_ms)"
```
Expected: `500`

- [ ] **Step 3: Commit**

```bash
git add src/smartcs/shared/config.py
git commit -m "feat: add AssistSettings configuration"
```

---

### Task 2: ORM 模型 — ScriptTemplate

**Files:**
- Modify: `src/smartcs/shared/orm_models.py` (append at end)

- [ ] **Step 1: Add ScriptTemplate and ScriptUsageLog ORM models**

```python
# ── 话术模板枚举 ──

class ScriptStatus(str, PyEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


_script_status = SAEnum(ScriptStatus, name="script_status", create_constraint=True, validate_strings=True)


class ScriptTemplate(Base):
    """话术模板表"""

    __tablename__ = "script_template"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), primary_key=True, default=_uuid_v7,
    )
    script_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # 对应 IntentLabel
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 含占位符
    variables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # ["customer_name","card_type"]
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    status: Mapped[ScriptStatus] = mapped_column(_script_status, nullable=False, default=ScriptStatus.ACTIVE)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default="system")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now,
        onupdate=datetime.now, server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_script_template_category", "category"),
        Index("ix_script_template_status_priority", "status", "priority"),
    )


class ScriptUsageLog(Base):
    """话术使用统计表"""

    __tablename__ = "script_usage_log"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), primary_key=True, default=_uuid_v7,
    )
    script_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    pushed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    clicked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_script_usage_log_session", "session_id"),
        Index("ix_script_usage_log_created", "created_at"),
    )
```

- [ ] **Step 2: Import required types**

Ensure `JSON` is already imported from sqlalchemy (check top of file — it should be). If not, add it.

- [ ] **Step 3: Verify**

```bash
poetry run python -c "from smartcs.shared.orm_models import ScriptTemplate, ScriptUsageLog; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/smartcs/shared/orm_models.py
git commit -m "feat: add ScriptTemplate and ScriptUsageLog ORM models"
```

---

### Task 3: ORM 模型 — AlertRule + AlertLog

**Files:**
- Modify: `src/smartcs/shared/orm_models.py` (append after Task 2 models)

- [ ] **Step 1: Add AlertRule and AlertLog ORM models**

```python
# ── 质检规则枚举 ──

class AlertRuleCategory(str, PyEnum):
    COMPLIANCE = "COMPLIANCE"
    EMOTION = "EMOTION"
    SILENCE = "SILENCE"
    PROCESS = "PROCESS"


class AlertRuleLevel(str, PyEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


_alert_rule_category = SAEnum(AlertRuleCategory, name="alert_rule_category", create_constraint=True, validate_strings=True)
_alert_rule_level = SAEnum(AlertRuleLevel, name="alert_rule_level", create_constraint=True, validate_strings=True)


class AlertRule(Base):
    """质检规则表"""

    __tablename__ = "alert_rule"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), primary_key=True, default=_uuid_v7,
    )
    rule_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    category: Mapped[AlertRuleCategory] = mapped_column(_alert_rule_category, nullable=False)
    level: Mapped[AlertRuleLevel] = mapped_column(_alert_rule_level, nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)  # 正则或关键词
    message: Mapped[str] = mapped_column(Text, nullable=False)  # 告警提示文案
    suggestion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    status: Mapped[ScriptStatus] = mapped_column(_script_status, nullable=False, default=ScriptStatus.ACTIVE)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default="system")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now,
        onupdate=datetime.now, server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_alert_rule_category_status", "category", "status"),
    )


class AlertLog(Base):
    """质检告警日志表"""

    __tablename__ = "alert_log"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), primary_key=True, default=_uuid_v7,
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_alert_log_session", "session_id"),
        Index("ix_alert_log_created", "created_at"),
    )
```

- [ ] **Step 2: Verify**

```bash
poetry run python -c "from smartcs.shared.orm_models import AlertRule, AlertLog; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/smartcs/shared/orm_models.py
git commit -m "feat: add AlertRule and AlertLog ORM models"
```

---

### Task 4: Alembic 迁移

**Files:**
- Create: `alembic/versions/` (auto-named)

- [ ] **Step 1: Generate migration**

```bash
cd /Users/qiangli/CodeBuddy/agent_project && poetry run alembic revision --autogenerate -m "add_script_template_alert_rule_tables"
```

- [ ] **Step 2: Review migration file**

Check the generated migration under `alembic/versions/` has `create_table` for `script_template`, `script_usage_log`, `alert_rule`, `alert_log`.

- [ ] **Step 3: Run migration**

```bash
poetry run alembic upgrade head
```
Expected: migrations applied successfully.

- [ ] **Step 4: Verify in PG**

```bash
docker exec smartcs-postgres psql -U smartcs -d smartcs -c "\dt script_*" && docker exec smartcs-postgres psql -U smartcs -d smartcs -c "\dt alert_*"
```
Expected: list both tables.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/
git commit -m "chore: add migration for script_template, alert_rule tables"
```

---

### Task 5: ProductCatalog — 产品目录 + 匹配推荐

**Files:**
- Create: `src/smartcs/services/assist/product_catalog.py`
- Test: `tests/test_product_catalog.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_product_catalog.py
from __future__ import annotations

import pytest
from smartcs.services.assist.product_catalog import Product, ProductCatalog
from smartcs.shared.models import IntentLabel


@pytest.fixture
def catalog():
    return ProductCatalog()


def test_catalog_has_products(catalog):
    assert len(catalog.products) > 0


def test_match_by_intent_installment(catalog):
    results = catalog.match(intent=IntentLabel.INSTALLMENT_INQUIRY, top_k=2)
    assert len(results) > 0
    assert all(isinstance(p, Product) for p in results)


def test_match_returns_empty_for_no_match(catalog):
    results = catalog.match(intent=IntentLabel.CHITCHAT, top_k=2)
    assert results == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_product_catalog.py -v
```
Expected: FAIL (module not found)

- [ ] **Step 3: Implement ProductCatalog**

```python
# src/smartcs/services/assist/product_catalog.py
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


# 种子产品数据
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/test_product_catalog.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/smartcs/services/assist/product_catalog.py tests/test_product_catalog.py
git commit -m "feat: add ProductCatalog with seed data and intent-based matching"
```

---

### Task 6: ScriptService — 话术加载与检索

**Files:**
- Create: `src/smartcs/services/assist/script_service.py`
- Test: `tests/test_script_service.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_script_service.py
from __future__ import annotations

import pytest
from smartcs.services.assist.script_service import ScriptService
from smartcs.shared.models import IntentLabel


@pytest.fixture
def service():
    return ScriptService()


def test_load_scripts_from_memory(service):
    """无 PG 时从内存种子数据加载"""
    service.load_from_memory()
    assert len(service._scripts) > 0


def test_retrieve_by_intent_faq(service):
    service.load_from_memory()
    results = service.retrieve(intent=IntentLabel.FAQ, top_k=3)
    assert len(results) > 0
    for s in results:
        assert s["category"] == IntentLabel.FAQ.value


def test_retrieve_respects_top_k(service):
    service.load_from_memory()
    results = service.retrieve(intent=IntentLabel.INSTALLMENT_INQUIRY, top_k=1)
    assert len(results) <= 1


def test_retrieve_returns_empty_for_no_match(service):
    service.load_from_memory()
    results = service.retrieve(intent=IntentLabel.TRANSFER_AGENT, top_k=3)
    assert results == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_script_service.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement ScriptService (load + retrieve)**

```python
# src/smartcs/services/assist/script_service.py
"""话术模板管理与检索"""

from __future__ import annotations

import logging
import time
from typing import Any

from smartcs.shared.models import IntentLabel

logger = logging.getLogger(__name__)

# ── 种子话术数据 ──

_SEED_SCRIPTS: list[dict[str, Any]] = [
    # FAQ
    {"script_id": "S-FAQ-001", "category": "faq", "tags": ["年费", "减免"], "title": "年费政策说明",
     "content": "{customer_name}您好，我行信用卡年费政策为：普卡首年免年费，刷卡6次免次年。您可通过手机银行查询具体年费信息。",
     "variables": ["customer_name"], "priority": 8,
     "card_types": [], "customer_tiers": []},
    {"script_id": "S-FAQ-002", "category": "faq", "tags": ["积分", "兑换"], "title": "积分兑换说明",
     "content": "您的积分可在「信用卡APP-我的积分」中兑换礼品或抵扣年费，当前兑换比例为{points_ratio}。",
     "variables": ["points_ratio"], "priority": 7,
     "card_types": [], "customer_tiers": []},
    # bill_query
    {"script_id": "S-BILL-001", "category": "bill_query", "tags": ["账单", "还款"], "title": "账单查询回复",
     "content": "{customer_name}您好，您本期账单金额为{bill_amount}元，到期还款日为{due_date}，请及时还款。",
     "variables": ["customer_name", "bill_amount", "due_date"], "priority": 9,
     "card_types": [], "customer_tiers": []},
    {"script_id": "S-BILL-002", "category": "bill_query", "tags": ["最低还款"], "title": "最低还款说明",
     "content": "您本期最低还款额为{min_amount}元。温馨提示：选择最低还款将产生利息，建议全额还款。",
     "variables": ["min_amount"], "priority": 8,
     "card_types": [], "customer_tiers": []},
    # installment_inquiry
    {"script_id": "S-INST-001", "category": "installment_inquiry", "tags": ["分期", "手续费"], "title": "分期方案介绍",
     "content": "您的{bill_amount}元账单可分{tenor_options}期，每期手续费率约{rate}%。目前我行有分期优惠活动，具体以页面显示为准。",
     "variables": ["bill_amount", "tenor_options", "rate"], "priority": 9,
     "card_types": [], "customer_tiers": []},
    # limit_query
    {"script_id": "S-LIMIT-001", "category": "limit_query", "tags": ["额度", "查询"], "title": "额度查询回复",
     "content": "您当前信用额度为{credit_limit}元，可用额度为{available_limit}元。如需提额，可在APP提交申请。",
     "variables": ["credit_limit", "available_limit"], "priority": 8,
     "card_types": [], "customer_tiers": []},
    # card_loss
    {"script_id": "S-LOSS-001", "category": "card_loss", "tags": ["挂失", "紧急"], "title": "挂失引导",
     "content": "已为您锁定卡片，请确认以下信息：最后交易时间{last_txn_time}，交易金额{last_txn_amount}元是否为本人操作？",
     "variables": ["last_txn_time", "last_txn_amount"], "priority": 10,
     "card_types": [], "customer_tiers": []},
    # complaint
    {"script_id": "S-COMP-001", "category": "complaint", "tags": ["投诉", "安抚"], "title": "投诉安抚",
     "content": "非常抱歉给您带来不好的体验。我已记录您反馈的问题，会加急处理并在24小时内回复您。",
     "variables": [], "priority": 10,
     "card_types": [], "customer_tiers": []},
    # chitchat
    {"script_id": "S-CHAT-001", "category": "chitchat", "tags": ["问候", "开场"], "title": "标准开场",
     "content": "您好，我是您的客户经理，很高兴为您服务。请问有什么可以帮您的？",
     "variables": [], "priority": 5,
     "card_types": [], "customer_tiers": []},
    {"script_id": "S-CHAT-002", "category": "chitchat", "tags": ["结束", "告别"], "title": "标准结束语",
     "content": "感谢您的来电，如有其他问题随时联系我们。祝您生活愉快！",
     "variables": [], "priority": 5,
     "card_types": [], "customer_tiers": []},
    # 通用FAQ
    {"script_id": "S-FAQ-003", "category": "faq", "tags": ["安全", "盗刷"], "title": "安全提示",
     "content": "如发现异常交易请立即联系我行客服挂失。挂失前48小时内非本人交易可申请赔付。",
     "variables": [], "priority": 7,
     "card_types": [], "customer_tiers": []},
    {"script_id": "S-FAQ-004", "category": "faq", "tags": ["手续费", "取现"], "title": "取现手续费说明",
     "content": "信用卡取现手续费为取现金额的{cash_advance_fee_rate}%，最低{cash_advance_min_fee}元/笔，并按日计息。",
     "variables": ["cash_advance_fee_rate", "cash_advance_min_fee"], "priority": 6,
     "card_types": [], "customer_tiers": []},
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
        from smartcs.shared.orm_models import ScriptTemplate, ScriptStatus

        result = await db_session.execute(
            select(ScriptTemplate).where(ScriptTemplate.status == ScriptStatus.ACTIVE)
        )
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

    @property
    def loaded_at(self) -> float:
        return self._loaded_at
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/test_script_service.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/smartcs/services/assist/script_service.py tests/test_script_service.py
git commit -m "feat: add ScriptService with memory loading and intent-based retrieval"
```

---

### Task 7: ScriptService — LLM 润色 + 变量解析

**Files:**
- Modify: `src/smartcs/services/assist/script_service.py` (add methods)
- Modify: `tests/test_script_service.py` (add tests)

- [ ] **Step 1: Write failing test**

```python
# Append to tests/test_script_service.py

def test_resolve_variables(service):
    service.load_from_memory()
    variables = {"customer_name": "王先生", "bill_amount": "3256.80"}
    # 取一条话术，手动设置变量验证解析
    script = service.retrieve(intent=IntentLabel.BILL_QUERY, top_k=1)[0]
    result = service.resolve_variables(script, variables)
    assert "王先生" in result
    assert "3256.80" in result
    assert "{" not in result  # 无残留占位符


def test_resolve_variables_no_placeholders(service):
    service.load_from_memory()
    result = service.resolve_variables({"content": "您好", "variables": []}, {"customer_name": "test"})
    assert result == "您好"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_script_service.py::test_resolve_variables -v
```
Expected: FAIL

- [ ] **Step 3: Add resolve_variables method**

```python
# Add these methods to ScriptService class in script_service.py

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
        """LLM 润色话术（可选，当前 if 非生产则跳过 LLM 直接返回原文）

        润色原则：
        - 保持原意不变
        - 语气亲切自然
        - 不添加未经确认的信息
        """
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
        except asyncio.TimeoutError:
            logger.warning("LLM 润色超时，返回原文")
            return script_content
        except Exception as e:
            logger.warning("LLM 润色失败: %s，返回原文", e)
            return script_content
```

Also add `import asyncio` at the top of the file.

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/test_script_service.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/smartcs/services/assist/script_service.py tests/test_script_service.py
git commit -m "feat: add script variable resolution and LLM polishing"
```

---

### Task 8: AlertEngine — 合规+情绪+趋势

**Files:**
- Create: `src/smartcs/services/assist/alert_engine.py`
- Test: `tests/test_alert_engine.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_alert_engine.py
from __future__ import annotations

import pytest
from smartcs.services.assist.alert_engine import AlertEngine
from smartcs.shared.models import SentimentLabel, AlertLevel, AlertCategory


@pytest.fixture
def engine():
    return AlertEngine()


def test_load_rules_from_memory(engine):
    engine.load_from_memory()
    assert len(engine._rules) > 0


def test_compliance_check_hits_keyword(engine):
    engine.load_from_memory()
    alerts = engine.check_compliance("这是非法套现渠道，包过")
    assert len(alerts) > 0
    assert alerts[0]["level"] != AlertLevel.INFO.value


def test_compliance_check_clean_text(engine):
    engine.load_from_memory()
    alerts = engine.check_compliance("您好，我想查询一下我的账单")
    assert alerts == []


def test_sentiment_alert_angry(engine):
    result = engine.check_sentiment(SentimentLabel.ANGRY)
    assert len(result) > 0
    assert result[0]["category"] == "emotion"


def test_sentiment_alert_neutral(engine):
    result = engine.check_sentiment(SentimentLabel.NEUTRAL)
    assert result == []


def test_trend_escalation(engine):
    history = [
        SentimentLabel.NEUTRAL,
        SentimentLabel.NEGATIVE,
        SentimentLabel.NEGATIVE,
        SentimentLabel.NEGATIVE,
    ]
    result = engine.check_sentiment_trend(history, window=3)
    assert len(result) > 0


def test_trend_no_escalation(engine):
    result = engine.check_sentiment_trend(
        [SentimentLabel.NEUTRAL, SentimentLabel.NEGATIVE],
        window=3,
    )
    assert result == []
```

- [ ] **Step 2: Run test**

```bash
poetry run pytest tests/test_alert_engine.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement AlertEngine**

```python
# src/smartcs/services/assist/alert_engine.py
"""质检告警引擎

合规检查 + 情绪检测 + 趋势分析 + 告警聚合。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from smartcs.shared.models import AlertCategory, AlertLevel, SentimentLabel

logger = logging.getLogger(__name__)

# ── 种子合规规则 ──

_SEED_RULES: list[dict[str, Any]] = [
    {
        "rule_id": "R-COMP-001", "category": "compliance", "level": "critical",
        "pattern": r"(套现|提额.*包过|内部渠道|免审核|无视征信)",
        "message": "检测到疑似违规承诺或套现引导",
        "suggestion": "请立即停止并警告客户此类行为违反监管规定",
        "priority": 10,
    },
    {
        "rule_id": "R-COMP-002", "category": "compliance", "level": "critical",
        "pattern": r"(1[3-9]\d{9}|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{4})",
        "message": "对话中疑似泄露客户身份证号",
        "suggestion": "请避免在对话中传输完整身份证号，使用脱敏格式",
        "priority": 9,
    },
    {
        "rule_id": "R-COMP-003", "category": "compliance", "level": "warning",
        "pattern": r"(保证|承诺|100%|绝对|肯定.*批|必须.*过)",
        "message": "检测到过度承诺用语",
        "suggestion": "请使用客观表述，避免对审批结果做保证性承诺",
        "priority": 7,
    },
    {
        "rule_id": "R-COMP-004", "category": "compliance", "level": "warning",
        "pattern": r"(骂人|傻[逼Xx]|fuck|shit|垃圾银行|骗子)",
        "message": "检测到不文明用语",
        "suggestion": "请保持专业态度，必要时转交主管处理",
        "priority": 6,
    },
    {
        "rule_id": "R-COMP-005", "category": "compliance", "level": "warning",
        "pattern": r"(1[3-9]\d)\d{4}(\d{4})",  # 手机号脱敏检测
        "message": "对话中可能存在未脱敏的手机号",
        "suggestion": "请确认手机号已脱敏（如：138****5678）",
        "priority": 8,
    },
    {
        "rule_id": "R-COMP-006", "category": "compliance", "level": "info",
        "pattern": r"(密码|pin|CVV|cvv|有效期.*卡)",
        "message": "对话涉及敏感卡片信息",
        "suggestion": "请勿在对话中记录或传输 CVV、密码等敏感信息",
        "priority": 8,
    },
]


class AlertEngine:
    """质检告警引擎"""

    def __init__(self) -> None:
        self._rules: list[dict[str, Any]] = []

    def load_from_memory(self, rules: list[dict[str, Any]] | None = None) -> None:
        self._rules = list(rules or _SEED_RULES)
        logger.info("从内存加载 %d 条质检规则", len(self._rules))

    async def load_from_db(self, db_session) -> None:
        from sqlalchemy import select
        from smartcs.shared.orm_models import AlertRule, ScriptStatus

        result = await db_session.execute(
            select(AlertRule).where(AlertRule.status == ScriptStatus.ACTIVE)
        )
        rows = result.scalars().all()
        self._rules = [
            {
                "rule_id": r.rule_id,
                "category": r.category.value.lower(),
                "level": r.level.value.lower(),
                "pattern": r.pattern,
                "message": r.message,
                "suggestion": r.suggestion,
                "priority": r.priority,
            }
            for r in rows
        ]
        logger.info("从数据库加载 %d 条质检规则", len(self._rules))

    def check_compliance(self, text: str) -> list[dict[str, Any]]:
        """合规检查：正则匹配告警规则"""
        alerts = []
        for rule in self._rules:
            if rule["category"] == "compliance":
                try:
                    if re.search(rule["pattern"], text, re.IGNORECASE):
                        alerts.append({
                            "level": rule["level"],
                            "category": "compliance",
                            "message": rule["message"],
                            "suggestion": rule["suggestion"],
                    })
                except re.error as e:
                    logger.warning("规则 %s 正则错误: %s", rule["rule_id"], e)
        return alerts

    def check_sentiment(self, sentiment: SentimentLabel) -> list[dict[str, Any]]:
        """情绪检测：负面/愤怒触发告警"""
        if sentiment == SentimentLabel.ANGRY:
            return [{
                "level": AlertLevel.CRITICAL.value,
                "category": "emotion",
                "message": "客户情绪激动，请使用安抚话术",
                "suggestion": "先道歉安抚，表示理解和重视，承诺快速处理",
            }]
        if sentiment == SentimentLabel.NEGATIVE:
            return [{
                "level": AlertLevel.WARNING.value,
                "category": "emotion",
                "message": "客户情绪较低落/不满",
                "suggestion": "表达理解和同理心，积极解决问题",
            }]
        return []

    def check_sentiment_trend(
        self, history: list[SentimentLabel], window: int = 3
    ) -> list[dict[str, Any]]:
        """情绪趋势分析：连续 N 轮负面/愤怒 → 升级告警"""
        if len(history) < window:
            return []
        recent = history[-window:]
        negative_count = sum(
            1 for s in recent if s in (SentimentLabel.NEGATIVE, SentimentLabel.ANGRY)
        )
        if negative_count >= window:
            return [{
                "level": AlertLevel.CRITICAL.value,
                "category": "emotion",
                "message": f"客户连续 {window} 轮情绪不佳，建议升级处理",
                "suggestion": "转交主管或启动投诉处理流程",
            }]
        return []

    def check_all(
        self,
        text: str,
        sentiment: SentimentLabel,
        sentiment_history: list[SentimentLabel],
        trend_window: int = 3,
    ) -> list[dict[str, Any]]:
        """全量检查：合规 + 情绪 + 趋势"""
        alerts = []
        alerts.extend(self.check_compliance(text))
        alerts.extend(self.check_sentiment(sentiment))
        alerts.extend(self.check_sentiment_trend(sentiment_history, trend_window))
        return alerts
```

- [ ] **Step 4: Run test**

```bash
poetry run pytest tests/test_alert_engine.py -v
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/smartcs/services/assist/alert_engine.py tests/test_alert_engine.py
git commit -m "feat: add AlertEngine with compliance, sentiment, and trend detection"
```

---

### Task 9: AssistOrchestrator — 并行编排

**Files:**
- Create: `src/smartcs/services/assist/agent.py`
- Test: `tests/test_assist_agent.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_assist_agent.py
from __future__ import annotations

import pytest
from smartcs.services.assist.agent import AssistOrchestrator
from smartcs.services.assist.script_service import ScriptService
from smartcs.services.assist.alert_engine import AlertEngine
from smartcs.services.assist.product_catalog import ProductCatalog
from smartcs.shared.models import AssistPushMessage, SentimentLabel, IntentLabel


@pytest.fixture
def orchestrator():
    script_svc = ScriptService()
    script_svc.load_from_memory()
    alert_engine = AlertEngine()
    alert_engine.load_from_memory()
    product_catalog = ProductCatalog()
    return AssistOrchestrator(
        script_service=script_svc,
        alert_engine=alert_engine,
        product_catalog=product_catalog,
        llm_client=None,
        es_client=None,  # 不测知识检索
    )


@pytest.mark.asyncio
async def test_process_message_returns_push_message(orchestrator):
    result = await orchestrator.process(
        session_id="test-001",
        message="我想查一下我的账单",
        intent=IntentLabel.BILL_QUERY,
        sentiment=SentimentLabel.NEUTRAL,
        sentiment_history=[],
        context="客户来电",
    )
    assert isinstance(result, AssistPushMessage)
    assert result.session_id == "test-001"
    assert result.type == "assist_push"


@pytest.mark.asyncio
async def test_process_message_has_scripts(orchestrator):
    result = await orchestrator.process(
        session_id="test-002",
        message="分期怎么办理",
        intent=IntentLabel.INSTALLMENT_INQUIRY,
        sentiment=SentimentLabel.NEUTRAL,
        sentiment_history=[],
        context="客户咨询分期业务",
    )
    assert len(result.payload.scripts) > 0


@pytest.mark.asyncio
async def test_process_message_triggers_compliance_alert(orchestrator):
    result = await orchestrator.process(
        session_id="test-003",
        message="我可以帮你套现，包过",
        intent=IntentLabel.FAQ,
        sentiment=SentimentLabel.NEUTRAL,
        sentiment_history=[],
        context="测试",
    )
    assert len(result.payload.alerts) > 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/test_assist_agent.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement AssistOrchestrator**

```python
# src/smartcs/services/assist/agent.py
"""坐席辅助编排器

接收客户消息 → 并行分发(话术/知识/质检/产品) → 汇聚 → 节流 → 推送。
纯 asyncio 实现，不依赖 LangGraph。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from smartcs.services.assist.alert_engine import AlertEngine
from smartcs.services.assist.product_catalog import ProductCatalog
from smartcs.services.assist.script_service import ScriptService
from smartcs.shared.config import get_settings
from smartcs.shared.models import (
    AlertObject,
    AssistPushMessage,
    AssistPushPayload,
    IntentLabel,
    KnowledgeSnippet,
    ProductRecommendation,
    ScriptCard,
    SentimentLabel,
)

logger = logging.getLogger(__name__)


class AssistOrchestrator:
    """坐席辅助编排器

    四路并行分支，各独立超时+降级。
    """

    def __init__(
        self,
        script_service: ScriptService,
        alert_engine: AlertEngine,
        product_catalog: ProductCatalog,
        llm_client=None,
        es_client=None,
        milvus_collection=None,
        embedding_provider=None,
        reranker=None,
    ) -> None:
        self._script_service = script_service
        self._alert_engine = alert_engine
        self._product_catalog = product_catalog
        self._llm_client = llm_client
        # 检索依赖（直接传参给 retrieve() 函数）
        self._es_client = es_client
        self._milvus_collection = milvus_collection
        self._embedding_provider = embedding_provider
        self._reranker = reranker
        self._last_push: dict[str, float] = {}  # session_id → last push timestamp
        self._settings = get_settings().assist

    async def process(
        self,
        session_id: str,
        message: str,
        intent: IntentLabel,
        sentiment: SentimentLabel,
        sentiment_history: list[SentimentLabel],
        context: str = "",
        variables: dict[str, str] | None = None,
    ) -> AssistPushMessage:
        """处理单条消息，返回推送消息"""
        t_start = time.monotonic()
        variables = variables or {}

        # ── 并行分发 ──
        async def _script_branch():
            return await self._run_script_branch(intent, context, variables)

        async def _knowledge_branch():
            return await self._run_knowledge_branch(message, intent)

        async def _alert_branch():
            return self._alert_engine.check_all(message, sentiment, sentiment_history, self._settings.sentiment_trend_window)

        async def _product_branch():
            return await self._run_product_branch(intent)

        script_result, knowledge_result, alert_result, product_result = await _parallel_dispatch(
            _script_branch(),
            _knowledge_branch(),
            _alert_branch(),
            _product_branch(),
            timeouts=(
                self._settings.script_timeout_ms / 1000,
                self._settings.knowledge_timeout_ms / 1000,
                self._settings.alert_timeout_ms / 1000,
                self._settings.product_timeout_ms / 1000,
            ),
        )

        # ── 组装推送载荷 ──
        scripts = [ScriptCard(**s) if isinstance(s, dict) else s for s in script_result]
        knowledge = [KnowledgeSnippet(**k) if isinstance(k, dict) else k for k in knowledge_result]
        alerts = alert_result
        products = product_result

        payload = AssistPushPayload(
            scripts=scripts,
            knowledge=knowledge,
            alerts=alerts,
            recommendations=products,
        )

        elapsed = (time.monotonic() - t_start) * 1000
        logger.info(
            "assist orchestration session=%s intent=%s scripts=%d knowledge=%d alerts=%d products=%d elapsed=%.1fms",
            session_id, intent.value, len(scripts), len(knowledge), len(alerts), len(products), elapsed,
        )

        return AssistPushMessage(
            session_id=session_id,
            timestamp=datetime.now(),
            trigger="customer_message",
            payload=payload,
        )

    async def _run_script_branch(
        self, intent: IntentLabel, context: str, variables: dict[str, str]
    ) -> list[dict]:
        scripts = self._script_service.retrieve(intent, top_k=self._settings.max_scripts_per_push)
        if not scripts:
            return []
        result = []
        for s in scripts:
            resolved = self._script_service.resolve_variables(s, variables)
            if self._llm_client:
                try:
                    resolved = await self._script_service.polish(
                        resolved, context, self._llm_client,
                        timeout_ms=self._settings.script_timeout_ms - 50,
                    )
                except Exception:
                    pass  # 润色失败用原文
            result.append({
                "script_id": s["script_id"],
                "content": resolved,
                "tags": s.get("tags", []),
                "priority": s.get("priority", 5),
            })
        return result

    async def _run_knowledge_branch(self, message: str, intent: IntentLabel) -> list[dict]:
        if not self._es_client:
            return []
        try:
            from smartcs.shared.models import RetrieveRequest
            from smartcs.services.common.retrieval import retrieve

            req = RetrieveRequest(query=message, top_k=self._settings.max_knowledge_per_push, rerank=True)
            resp = await retrieve(
                request=req,
                es_client=self._es_client,
                milvus_collection=self._milvus_collection,
                embedding_provider=self._embedding_provider,
                reranker=self._reranker,
            )
            return [
                {
                    "chunk_id": c.chunk_id,
                    "summary": c.content[:200],
                    "source": c.source_doc,
                    "confidence": "high" if c.score > 0.8 else "medium" if c.score > 0.5 else "low",
                }
                for c in resp.results
            ]
        except Exception as e:
            logger.warning("知识检索失败: %s", e)
            return []

    async def _run_product_branch(self, intent: IntentLabel) -> list[dict]:
        products = self._product_catalog.match(intent, top_k=self._settings.max_recommendations_per_push)
        return [
            {
                "product_id": p.product_id,
                "product_name": p.product_name,
                "reason": p.description,
                "script_suggestion": p.script_template,
                "risk_tip": p.risk_tip,
                "eligibility_match": True,
            }
            for p in products
        ]

    def should_throttle(self, session_id: str) -> bool:
        """检查是否需要节流"""
        now = time.monotonic()
        last = self._last_push.get(session_id, 0)
        if now - last < self._settings.throttle_window_ms / 1000:
            return True
        self._last_push[session_id] = now
        return False

    def force_reset_throttle(self, session_id: str) -> None:
        """重置节流计时器（告警消息不受节流限制）"""
        self._last_push.pop(session_id, None)


async def _parallel_dispatch(
    script_coro,
    knowledge_coro,
    alert_coro,
    product_coro,
    timeouts: tuple[float, float, float, float],
) -> tuple[list, list, list, list]:
    """并行执行四路分支，各独立超时，单路失败不影响其他"""

    async def _run_with_timeout(coro, timeout: float, label: str, default):
        try:
            if timeout > 0:
                return await asyncio.wait_for(coro, timeout=timeout)
            return await coro
        except asyncio.TimeoutError:
            logger.warning("分支 %s 超时 (%.1fs)，触发降级", label, timeout)
            return default
        except Exception as e:
            logger.warning("分支 %s 异常: %s，触发降级", label, e)
            return default

    results = await asyncio.gather(
        _run_with_timeout(script_coro, timeouts[0], "script", []),
        _run_with_timeout(knowledge_coro, timeouts[1], "knowledge", []),
        _run_with_timeout(alert_coro, timeouts[2], "alert", []),
        _run_with_timeout(product_coro, timeouts[3], "product", []),
    )
    return tuple(results)  # type: ignore[return-value]
```

- [ ] **Step 4: Run test**

```bash
poetry run pytest tests/test_assist_agent.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/smartcs/services/assist/agent.py tests/test_assist_agent.py
git commit -m "feat: add AssistOrchestrator with parallel dispatch and per-branch timeout"
```

---

### Task 10: WebSocket Router 重写

**Files:**
- Modify: `src/smartcs/services/assist/router.py`

- [ ] **Step 1: Rewrite router.py**

```python
"""坐席辅助服务 HTTP/WebSocket 路由"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from smartcs.shared.models import (
    AssistPushMessage,
    IntentLabel,
    SentimentLabel,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assist"])


@router.get("/health")
async def health_check():
    """坐席辅助服务健康检查"""
    return {"status": "healthy", "service": "assist"}


@router.websocket("/ws/{session_id}")
async def assist_websocket(websocket: WebSocket, session_id: str):
    """坐席辅助 WebSocket

    生命周期：
    1. 鉴权（token 参数验证）
    2. 心跳（ping/pong 15s）
    3. 接收 client 消息 → 编排处理 → 推送结果
    """
    await websocket.accept()

    # 获取依赖
    app = websocket.app
    orchestrator = app.state.assist_orchestrator
    session_manager = app.state.session_manager

    # 加载会话历史
    session_state = None
    try:
        session_state = await session_manager.load(session_id)
    except Exception as e:
        logger.warning("加载会话 %s 失败: %s", session_id, e)

    sentiment_history: list[SentimentLabel] = []
    if session_state:
        for turn in session_state.turns:
            if turn.emotion_label and turn.speaker == "customer":
                sentiment_history.append(turn.emotion_label)

    # 发送就绪消息
    await websocket.send_json({
        "type": "assist_ready",
        "session_id": session_id,
        "message": "坐席辅助服务就绪",
    })

    # 心跳任务
    heartbeat_task = asyncio.create_task(_heartbeat(websocket))

    try:
        while True:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "无效的 JSON"})
                continue

            msg_type = data.get("type", "customer_message")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "customer_message":
                message = data.get("message", "")
                intent_str = data.get("intent", "faq")
                sentiment_str = data.get("sentiment", "neutral")
                context = data.get("context", message)

                try:
                    intent = IntentLabel(intent_str)
                    sentiment = SentimentLabel(sentiment_str)
                except ValueError:
                    intent = IntentLabel.FAQ
                    sentiment = SentimentLabel.NEUTRAL

                variables = data.get("variables", {})

                t0 = time.monotonic()
                push_msg = await orchestrator.process(
                    session_id=session_id,
                    message=message,
                    intent=intent,
                    sentiment=sentiment,
                    sentiment_history=sentiment_history,
                    context=context,
                    variables=variables,
                )
                elapsed = time.monotonic() - t0

                # 更新 sentiment_history
                sentiment_history.append(sentiment)
                if len(sentiment_history) > 20:
                    sentiment_history = sentiment_history[-20:]

                # 有告警 → 不受节流限制
                has_critical_alerts = any(a.get("level") == "critical" for a in push_msg.payload.alerts)
                if has_critical_alerts or not orchestrator.should_throttle(session_id):
                    await websocket.send_json(push_msg.model_dump(mode="json"))
                    logger.debug("推送至 session=%s, elapsed=%.1fms", session_id, elapsed * 1000)
                else:
                    logger.debug("节流跳过 session=%s", session_id)

    except asyncio.TimeoutError:
        logger.info("WebSocket session=%s 超时关闭", session_id)
    except WebSocketDisconnect:
        logger.info("WebSocket session=%s 客户端断开", session_id)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _heartbeat(websocket: WebSocket, interval: float = 15.0):
    """心跳发送"""
    while True:
        await asyncio.sleep(interval)
        try:
            await websocket.send_json({"type": "heartbeat"})
        except Exception:
            break
```

- [ ] **Step 2: Verify imports work**

```bash
poetry run python -c "from smartcs.services.assist.router import router; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/smartcs/services/assist/router.py
git commit -m "feat: rewrite assist WebSocket with orchestration, heartbeat, and throttle"
```

---

### Task 11: 依赖注入 + Lifespan 集成

**Files:**
- Modify: `src/smartcs/services/common/deps.py`
- Modify: `src/smartcs/main.py`

- [ ] **Step 1: Add init/close functions in deps.py**

Add these after the existing `init_agent` / `close_agent` functions:

```python
# ── 坐席辅助编排器 ──


async def init_assist_orchestrator(app) -> None:
    """初始化坐席辅助编排器"""
    from smartcs.services.assist.agent import AssistOrchestrator
    from smartcs.services.assist.alert_engine import AlertEngine
    from smartcs.services.assist.product_catalog import ProductCatalog
    from smartcs.services.assist.script_service import ScriptService

    # 话术服务（从 DB 加载，fallback 到内存）
    script_service = ScriptService()
    try:
        session_factory = app.state.db_session_factory
        async with session_factory() as db_session:
            await script_service.load_from_db(db_session)
    except Exception as e:
        _logger.warning("从数据库加载话术失败，使用内存种子数据: %s", e)
        script_service.load_from_memory()

    # 告警引擎
    alert_engine = AlertEngine()
    try:
        session_factory = app.state.db_session_factory
        async with session_factory() as db_session:
            await alert_engine.load_from_db(db_session)
    except Exception as e:
        _logger.warning("从数据库加载告警规则失败，使用内存种子数据: %s", e)
        alert_engine.load_from_memory()

    # 产品目录
    product_catalog = ProductCatalog()

    # 检索依赖（直接传参给 retrieve() 函数）
    es_client = getattr(app.state, "es_client", None)
    milvus_col = getattr(app.state, "milvus_collection", None)
    embedding_provider = getattr(app.state, "embedding_provider", None)
    reranker = getattr(app.state, "reranker_provider", None)

    llm_client = getattr(app.state, "llm_client", None)

    orchestrator = AssistOrchestrator(
        script_service=script_service,
        alert_engine=alert_engine,
        product_catalog=product_catalog,
        llm_client=llm_client,
        es_client=es_client,
        milvus_collection=milvus_col,
        embedding_provider=embedding_provider,
        reranker=reranker,
    )
    app.state.assist_orchestrator = orchestrator
    _logger.info("坐席辅助编排器初始化完成")


async def close_assist_orchestrator(app) -> None:
    """关闭坐席辅助编排器"""
    app.state.assist_orchestrator = None
```

Then add the type alias:

```python
AssistOrchestratorDep = Annotated[Any, Depends(lambda r: r.app.state.assist_orchestrator)]
```

- [ ] **Step 2: Update main.py assist_lifespan**

In `src/smartcs/main.py`, add to the import from `smartcs.services.common.deps`:
```python
    init_assist_orchestrator,
    close_assist_orchestrator,
```

In `assist_lifespan` startup, after `init_llm` line add:
```python
    await init_llm(app)
    await init_session_manager(app)
    await init_assist_orchestrator(app)  # ADD THIS LINE
    logger.info("坐席辅助服务就绪")
```

In `assist_lifespan` shutdown, after `yield`, add:
```python
    await close_assist_orchestrator(app)  # ADD THIS LINE
    await close_session_manager(app)
    await close_llm(app)
```

- [ ] **Step 3: Verify startup**

```bash
make dev
```
Expected: Both services start, "坐席辅助编排器初始化完成" in logs.

- [ ] **Step 4: Commit**

```bash
git add src/smartcs/services/common/deps.py src/smartcs/main.py
git commit -m "feat: add assist orchestrator dependency injection and lifespan integration"
```

---

### Task 12: 前端 WebSocket 打通

**Files:**
- Modify: `web/src/composables/useWebSocket.ts`
- No backend changes needed (data contract already aligned)

- [ ] **Step 1: Check useWebSocket composable**

Read `web/src/composables/useWebSocket.ts` and verify it sends correct message format.

- [ ] **Step 2: Run frontend dev server**

```bash
cd web && pnpm dev
```

- [ ] **Step 3: Browser manual test**

Open `http://localhost:5174`, select a session, verify:
- WebSocket connects and shows "坐席辅助服务就绪"
- Mock customer messages trigger push data in assist panel

- [ ] **Step 4: Commit (if changes)**

```bash
git add web/
git commit -m "feat: wire assist WebSocket to frontend"
```

---

### Task 13: Seed data script

**Files:**
- Create: `scripts/seed_assist_data.py`

- [ ] **Step 1: Create seed script**

```python
# scripts/seed_assist_data.py
"""坐席辅助种子数据导入：话术 + 告警规则"""

from __future__ import annotations

import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from smartcs.shared.orm_models import (
    AlertRule,
    AlertRuleCategory,
    AlertRuleLevel,
    ScriptStatus,
    ScriptTemplate,
)


async def seed_scripts(db: AsyncSession) -> None:
    """导入话术种子数据"""
    from smartcs.services.assist.script_service import _SEED_SCRIPTS

    for s in _SEED_SCRIPTS:
        exists = await db.execute(
            select(ScriptTemplate).where(ScriptTemplate.script_id == s["script_id"])
        )
        if exists.scalar_one_or_none():
            continue
        db.add(ScriptTemplate(
            script_id=s["script_id"],
            category=s["category"],
            tags=s.get("tags", []),
            title=s.get("title", ""),
            content=s["content"],
            variables=s.get("variables", []),
            priority=s.get("priority", 5),
            status=ScriptStatus.ACTIVE,
            version=1,
            created_by="seed",
        ))
    await db.commit()
    print(f"Seeded {len(_SEED_SCRIPTS)} scripts")


async def seed_rules(db: AsyncSession) -> None:
    """导入告警规则种子数据"""
    from smartcs.services.assist.alert_engine import _SEED_RULES

    for r in _SEED_RULES:
        exists = await db.execute(
            select(AlertRule).where(AlertRule.rule_id == r["rule_id"])
        )
        if exists.scalar_one_or_none():
            continue
        db.add(AlertRule(
            rule_id=r["rule_id"],
            category=AlertRuleCategory[r["category"].upper()],
            level=AlertRuleLevel[r["level"].upper()],
            pattern=r["pattern"],
            message=r["message"],
            suggestion=r.get("suggestion", ""),
            priority=r.get("priority", 5),
            status=ScriptStatus.ACTIVE,
            created_by="seed",
        ))
    await db.commit()
    print(f"Seeded {len(_SEED_RULES)} alert rules")


async def main():
    from smartcs.services.common.database import _create_async_engine
    from smartcs.shared.config import get_settings

    settings = get_settings()
    engine = _create_async_engine(settings.database.dsn)
    from sqlalchemy.ext.asyncio import async_sessionmaker
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        await seed_scripts(session)
        await seed_rules(session)

    await engine.dispose()
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run seed script**

```bash
poetry run python scripts/seed_assist_data.py
```
Expected: "Seeded 12 scripts", "Seeded 6 alert rules", "Seed complete."

- [ ] **Step 3: Commit**

```bash
git add scripts/seed_assist_data.py
git commit -m "chore: add assist seed data script for scripts and alert rules"
```

---

### Task 14: 端到端验证

- [ ] **Step 1: Restart all services**

```bash
# Terminal 1
make dev

# Terminal 2
cd web && pnpm dev
```

- [ ] **Step 2: Test WebSocket manually**

```bash
# 用 wscat 或 curl 测试
# 1. 连接 WebSocket，验证心跳
# 2. 发送 customer_message，验证推送响应
```

- [ ] **Step 3: Run full test suite**

```bash
poetry run pytest tests/ -v --ignore=tests/test_metrics.py -x
```
Expected: All tests pass.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: Sprint 5 complete — AssistOrchestrator with parallel dispatch and production-grade WebSocket"
```
