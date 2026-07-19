"""知识库 ORM 模型

定义知识库核心数据表：
- kb_document: 知识库文档元数据
- kb_chunk: 文档分块
- kb_ingestion_log: 文档摄入流水日志

使用 SQLAlchemy 2.0 声明式映射，UUID v7 作为主键。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

import uuid_utils
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ── Base ──


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""


# ── Python 枚举 + PG ENUM 类型 ──


class KbSourceType(StrEnum):
    """文档来源类型"""

    PDF = "PDF"
    DOCX = "DOCX"
    HTML = "HTML"
    MARKDOWN = "MARKDOWN"
    TXT = "TXT"
    XLSX = "XLSX"


class KbDocStatus(StrEnum):
    """文档处理状态（技术层面）"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"
    KAFKA_PENDING = "KAFKA_PENDING"


class KbApprovalStatus(StrEnum):
    """文档审批状态（业务层面 — 银行合规要求）

    生命周期: DRAFT → IN_REVIEW → APPROVED → PUBLISHED → SUPERSEDED → ARCHIVED
                                                  ↘ REJECTED → DRAFT
    """

    DRAFT = "DRAFT"  # 草稿，编制中
    IN_REVIEW = "IN_REVIEW"  # 提交审核
    APPROVED = "APPROVED"  # 审核通过，待发布
    PUBLISHED = "PUBLISHED"  # 已发布，可被检索
    SUPERSEDED = "SUPERSEDED"  # 被新版本替代
    REJECTED = "REJECTED"  # 审核驳回
    ARCHIVED = "ARCHIVED"  # 归档


class KbEmbedStatus(StrEnum):
    """嵌入状态"""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class KbIngestionStage(StrEnum):
    """摄入流水阶段"""

    PARSE = "PARSE"
    CLEAN = "CLEAN"
    CHUNK = "CHUNK"
    EMBED = "EMBED"
    ES_WRITE = "ES_WRITE"
    MILVUS_WRITE = "MILVUS_WRITE"
    KAFKA_PUBLISH = "KAFKA_PUBLISH"


class KbIngestionStatus(StrEnum):
    """摄入流水状态"""

    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


# SQLAlchemy ENUM 列类型（映射到 PG ENUM）
_kb_source_type = SAEnum(KbSourceType, name="kb_source_type", create_constraint=True, validate_strings=True)
_kb_doc_status = SAEnum(KbDocStatus, name="kb_doc_status", create_constraint=True, validate_strings=True)
_kb_approval_status = SAEnum(KbApprovalStatus, name="kb_approval_status", create_constraint=True, validate_strings=True)
_kb_embed_status = SAEnum(KbEmbedStatus, name="kb_embed_status", create_constraint=True, validate_strings=True)
_kb_ingestion_stage = SAEnum(KbIngestionStage, name="kb_ingestion_stage", create_constraint=True, validate_strings=True)
_kb_ingestion_status = SAEnum(
    KbIngestionStatus, name="kb_ingestion_status", create_constraint=True, validate_strings=True
)


def _uuid_v7() -> uuid_utils.UUID:
    """生成 UUID v7（时序排序）"""
    return uuid_utils.uuid7()


# ── KbDocument ──


class KbDocument(Base):
    """知识库文档元数据表"""

    __tablename__ = "kb_document"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[KbSourceType] = mapped_column(_kb_source_type, nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    card_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    security_level: Mapped[str] = mapped_column(String(16), nullable=False, default="internal")
    version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[KbDocStatus] = mapped_column(_kb_doc_status, nullable=False, default=KbDocStatus.PENDING)

    # ── 银行合规字段 ──
    approval_status: Mapped[KbApprovalStatus] = mapped_column(
        _kb_approval_status,
        nullable=False,
        default=KbApprovalStatus.DRAFT,
    )
    doc_group: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="文档组 ID: 同一逻辑文档的不同版本共享同一 doc_group",
    )
    is_current_version: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否为当前生效版本（同一 doc_group 仅一个为 true）",
    )
    allowed_roles: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        default=list,
        comment="允许访问的角色列表，空表示所有角色可访问",
    )
    regulatory_tags: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        default=list,
        comment="监管标签，如 ['银保监〔2024〕XX号', 'PCI DSS']",
    )
    source_document_number: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="发文编号/制度编号，用于合规溯源",
    )
    last_review_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="上次内容复核日期",
    )
    next_review_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="下次复核截止日期，过期触发新鲜度告警",
    )

    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default="system")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("now()"),
    )

    # relationships
    chunks: Mapped[list[KbChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    ingestion_logs: Mapped[list[KbIngestionLog]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_kb_document_category", "category"),
        Index("ix_kb_document_status", "status"),
        Index("ix_kb_document_approval_status", "approval_status"),
        # content_hash 改为非唯一索引，支持版本共存（同内容不同版本可同时存在）
        Index(
            "ix_kb_document_content_hash",
            "content_hash",
            postgresql_where=text("content_hash IS NOT NULL"),
        ),
        # 部分唯一索引: 同一 doc_group 内仅一个 is_current_version=true
        Index(
            "ix_kb_document_current_version",
            "doc_group",
            unique=True,
            postgresql_where=text("is_current_version = true AND is_deleted = false"),
        ),
    )


# ── 文档审批工作流 ──


class KbApprovalAction(StrEnum):
    """审批动作类型"""

    CREATE = "CREATE"
    SUBMIT = "SUBMIT"  # DRAFT → IN_REVIEW
    APPROVE = "APPROVE"  # IN_REVIEW → APPROVED
    REJECT = "REJECT"  # IN_REVIEW → REJECTED
    PUBLISH = "PUBLISH"  # APPROVED → PUBLISHED
    SUPERSEDE = "SUPERSEDE"  # PUBLISHED → SUPERSEDED (被新版本替代)
    ARCHIVE = "ARCHIVE"  # → ARCHIVED


_kb_approval_action = SAEnum(KbApprovalAction, name="kb_approval_action", create_constraint=True, validate_strings=True)


class KbDocumentApproval(Base):
    """文档审批记录表（append-only，不可修改/删除）

    银行合规要求: 每次状态变更必须有完整审计链:
    编制人提交 → 审核人审核 → 批准人发布
    """

    __tablename__ = "kb_document_approval"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    document_id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        ForeignKey("kb_document.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[KbApprovalAction] = mapped_column(_kb_approval_action, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (Index("ix_kb_approval_document_created", "document_id", "created_at"),)


# ── KbChunk ──


class KbChunk(Base):
    """文档分块表"""

    __tablename__ = "kb_chunk"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    document_id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        ForeignKey("kb_document.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_status: Mapped[KbEmbedStatus] = mapped_column(
        _kb_embed_status, nullable=False, default=KbEmbedStatus.PENDING
    )
    es_indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    milvus_indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Parent-Child 分块字段
    parent_chunk_id: Mapped[uuid_utils.UUID | None] = mapped_column(
        Uuid(native_uuid=False),
        ForeignKey("kb_chunk.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_type: Mapped[str] = mapped_column(String(16), nullable=False, default="plain_text")
    heading_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()")
    )

    # relationships
    document: Mapped[KbDocument] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_kb_chunk_doc_index", "document_id", "chunk_index"),
        Index(
            "ix_kb_chunk_embedding_status",
            "embedding_status",
            postgresql_where=text("embedding_status = 'PENDING'"),
        ),
        Index(
            "ix_kb_chunk_es_indexed",
            "es_indexed",
            postgresql_where=text("es_indexed = false"),
        ),
        Index(
            "ix_kb_chunk_milvus_indexed",
            "milvus_indexed",
            postgresql_where=text("milvus_indexed = false"),
        ),
        Index("ix_kb_chunk_parent_chunk_id", "parent_chunk_id"),
    )


# ── KbIngestionLog ──


class KbIngestionLog(Base):
    """文档摄入流水日志表"""

    __tablename__ = "kb_ingestion_log"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    document_id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        ForeignKey("kb_document.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[KbIngestionStage] = mapped_column(_kb_ingestion_stage, nullable=False)
    status: Mapped[KbIngestionStatus] = mapped_column(_kb_ingestion_status, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_detail: Mapped[dict | None] = mapped_column("step_detail_json", JSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()")
    )

    # relationships
    document: Mapped[KbDocument] = relationship(back_populates="ingestion_logs")


# ── ScriptStatus ──


class ScriptStatus(StrEnum):
    """话术模板状态"""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


_script_status = SAEnum(ScriptStatus, name="script_status", create_constraint=True, validate_strings=True)


# ── ScriptTemplate ──


class ScriptTemplate(Base):
    """话术模板表"""

    __tablename__ = "script_template"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    script_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # 对应 IntentLabel
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 含占位符
    variables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    status: Mapped[ScriptStatus] = mapped_column(_script_status, nullable=False, default=ScriptStatus.ACTIVE)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default="system")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_script_template_category", "category"),
        Index("ix_script_template_status_priority", "status", "priority"),
    )


# ── ScriptUsageLog ──


class ScriptUsageLog(Base):
    """话术使用统计表"""

    __tablename__ = "script_usage_log"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    script_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    pushed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    clicked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_script_usage_log_session", "session_id"),
        Index("ix_script_usage_log_created", "created_at"),
    )


# ── 质检规则枚举 ──


class AlertRuleCategory(StrEnum):
    COMPLIANCE = "COMPLIANCE"
    EMOTION = "EMOTION"
    SILENCE = "SILENCE"
    PROCESS = "PROCESS"


class AlertRuleLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


_alert_rule_category = SAEnum(
    AlertRuleCategory, name="alert_rule_category", create_constraint=True, validate_strings=True
)
_alert_rule_level = SAEnum(AlertRuleLevel, name="alert_rule_level", create_constraint=True, validate_strings=True)


class AlertRule(Base):
    """质检规则表"""

    __tablename__ = "alert_rule"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
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
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (Index("ix_alert_rule_category_status", "category", "status"),)


class AlertLog(Base):
    """质检告警日志表"""

    __tablename__ = "alert_log"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_alert_log_session", "session_id"),
        Index("ix_alert_log_created", "created_at"),
    )


# ── 编排决策日志 ──


class OrchestrationLog(Base):
    """编排决策日志"""

    __tablename__ = "orchestration_logs"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    session_id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=True),
        nullable=False,
        index=True,
    )
    oe_state: Mapped[str] = mapped_column(String(20), nullable=False)
    d1_activated: Mapped[bool] = mapped_column(Boolean, default=False)
    d2_activated: Mapped[bool] = mapped_column(Boolean, default=False)
    d3_activated: Mapped[bool] = mapped_column(Boolean, default=True)
    activation_history: Mapped[list] = mapped_column(JSON, default=list)
    fusion_type: Mapped[str] = mapped_column(String(30), default="service_only")
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str] = mapped_column(String(36), default="")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )


# ── 反馈日志 ──


class FeedbackLog(Base):
    """反馈日志"""

    __tablename__ = "feedback_logs"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    session_id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=True),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, default=0.0)
    modify_fields: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )


# ── 产品目录 ──


class ProductStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class KbProduct(Base):
    """产品目录表 — 推荐营销产品"""

    __tablename__ = "kb_product"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid_utils.uuid7()),
    )
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="general")
    intents: Mapped[list] = mapped_column(JSON, default=list)  # 关联意图标签
    description: Mapped[str | None] = mapped_column(Text)
    eligibility_keywords: Mapped[list] = mapped_column(JSON, default=list)
    risk_tip: Mapped[str | None] = mapped_column(String(500))
    priority: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(20), default=ProductStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), onupdate=datetime.now)

    __table_args__ = (
        Index("idx_product_status", "status"),
        Index("idx_product_category", "category"),
    )


# ── FAQ 结构化知识库 ──


class KbFaq(Base):
    """FAQ 问答对表（结构化存储，非文档）

    每条记录是一个独立的 Q&A 检索单元，不需要分块。
    直接索引到 ES + Milvus，走独立检索路径。
    """

    __tablename__ = "kb_faq"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    question: Mapped[str] = mapped_column(String(512), nullable=False, comment="标准问题")
    answer: Mapped[str] = mapped_column(Text, nullable=False, comment="标准答案（Markdown）")
    variant_questions: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment='同义问句列表 ["年费能省吗", "年费怎么退"]',
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False, comment="分类: 年费/积分/账单/...")
    card_types: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment="适用卡种，空=通用",
    )
    customer_tiers: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment="适用客户层级，空=通用",
    )
    keywords: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment="检索关键词",
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="同分类排序权重")

    # 审批 + 版本
    doc_group: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    approval_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="DRAFT",
        comment="DRAFT/IN_REVIEW/APPROVED/PUBLISHED/SUPERSEDED/REJECTED/ARCHIVED",
    )
    is_current_version: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # 合规
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    allowed_roles: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    regulatory_tags: Mapped[list] = mapped_column(JSON, nullable=True, default=list)

    # 审计
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default="system")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_kb_faq_category", "category"),
        Index("ix_kb_faq_approval_status", "approval_status"),
        Index("ix_kb_fqa_published", "approval_status", "is_current_version", "is_deleted"),
        Index(
            "ix_kb_faq_current_version",
            "doc_group",
            unique=True,
            postgresql_where=text("is_current_version = true AND is_deleted = false"),
        ),
    )


class KbFaqSearchLog(Base):
    """FAQ 检索日志（分析用）"""

    __tablename__ = "kb_faq_search_log"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    query: Mapped[str] = mapped_column(String(512), nullable=False)
    match_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="exact/semantic/miss")
    faq_id: Mapped[uuid_utils.UUID | None] = mapped_column(Uuid(native_uuid=False), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    user_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_kb_faq_log_created", "created_at"),
        Index("ix_kb_faq_log_faq", "faq_id"),
        Index("ix_kb_faq_log_match_type", "match_type"),
    )


# ── 聊天消息审计 ──


class ChatMessageStatus(StrEnum):
    """消息处理状态"""

    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    SKIPPED = "skipped"
    ERROR = "error"


_chat_message_status = SAEnum(
    ChatMessageStatus, name="chat_message_status", create_constraint=True, validate_strings=True
)


class ChatMessage(Base):
    """聊天消息审计表

    所有客户端消息全量落库，支持：
    - 审计合规（ACID 保证，不可删除）
    - 全文搜索（tsvector + GIN 索引）
    - 快速意图分类记录
    """

    __tablename__ = "chat_message"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True, default="web")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    quick_intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    processing_status: Mapped[ChatMessageStatus] = mapped_column(
        _chat_message_status,
        nullable=False,
        default=ChatMessageStatus.QUEUED,
    )
    processing_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata_json", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_chat_message_session", "session_id"),
        Index("ix_chat_message_created", "created_at"),
        Index("ix_chat_message_intent", "intent"),
        Index(
            "ix_chat_message_content_fts",
            text("to_tsvector('simple', content)"),
            postgresql_using="gin",
        ),
    )


# ── 意图检测规则 ──


class IntentDetectionRule(Base):
    """意图检测规则表

    Phase 3 L1 规则引擎的配置存储，支持 Redis Pub/Sub 热加载。
    """

    __tablename__ = "intent_detection_rule"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    rule_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)  # 意图域: account/transaction/card/complaint/chat
    patterns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 正则列表 ["转账", "汇款", ...]
    keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 关键词列表
    negation_of: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 覆盖哪个域
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.85)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (Index("ix_intent_rule_domain_status", "domain", "status"),)


# ── 操作审计日志 ──


class AuditLog(Base):
    """操作审计日志表

    记录所有状态变更操作（会话转换、反馈提交、文档上传、配置修改等），
    满足银行合规审计要求。append-only，不可修改/删除。
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)  # 操作者 ID（user_id 或 service）
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)  # customer/agent/admin/service
    action: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # 操作类型: session.transition/feedback.submit/doc.upload
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)  # session/document/feedback/config
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 操作目标 ID
    method: Mapped[str] = mapped_column(String(8), nullable=False)  # HTTP method
    path: Mapped[str] = mapped_column(String(256), nullable=False)  # 请求路径
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)  # HTTP 响应码
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 操作详情（变更前后等）
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_audit_log_timestamp", "timestamp"),
        Index("ix_audit_log_actor", "actor_id"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_target", "target_type", "target_id"),
    )


class DialogueLog(Base):
    """对话记录持久化表

    会话结束时从 Redis 异步落库，满足银行合规审计要求（保存 5-7 年）。
    每轮对话一条记录，包含完整决策上下文（意图/实体/检索来源）。
    """

    __tablename__ = "dialogue_log"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False),
        primary_key=True,
        default=_uuid_v7,
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)  # customer/agent/bot
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    entities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    response_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retrieval_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    emotion_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    emotion_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
    )
    customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=datetime.now,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_dialogue_log_session", "session_id"),
        Index("ix_dialogue_log_session_ts", "session_id", "timestamp"),
        Index("ix_dialogue_log_customer", "customer_id"),
        Index("ix_dialogue_log_speaker", "speaker"),
    )
