"""知识库 ORM 模型

核心表：
- kb_document: 文档元数据（含审批/合规字段）
- kb_chunk: 文档分块（含 embedding 向量列 + model_version 嵌入版本治理）
- kb_ingestion_log: 摄入流水日志
- kb_document_approval: 审批工作流审计

PG 是唯一真相源：chunk 正文 + embedding 向量都在 PG。
ES 是可从 PG 重建的派生索引。
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
    LargeBinary,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.orm.base import Base


# ── 枚举 ──


class KbSourceType(StrEnum):
    """文档来源类型"""

    PDF = "PDF"
    DOCX = "DOCX"
    HTML = "HTML"
    MARKDOWN = "MARKDOWN"
    TXT = "TXT"
    XLSX = "XLSX"


class KbDocStatus(StrEnum):
    """文档处理状态"""

    PENDING = "PENDING"
    KAFKA_QUEUED = "KAFKA_QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class KbApprovalStatus(StrEnum):
    """文档审批状态（银行合规）

    DRAFT → IN_REVIEW → APPROVED → PUBLISHED → SUPERSEDED → ARCHIVED
                                              ↘ REJECTED → DRAFT
    """

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class KbApprovalAction(StrEnum):
    """审批动作"""

    CREATE = "CREATE"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    PUBLISH = "PUBLISH"
    SUPERSEDE = "SUPERSEDE"
    ARCHIVE = "ARCHIVE"


class KbEmbedStatus(StrEnum):
    """嵌入状态"""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class KbIngestionStage(StrEnum):
    """摄入流水阶段

    7 阶段：PARSE → CLEAN → EXTRACT → CHUNK → EMBED → ES_WRITE → KAFKA_PUBLISH
    """

    PARSE = "PARSE"
    CLEAN = "CLEAN"
    EXTRACT = "EXTRACT"
    CHUNK = "CHUNK"
    EMBED = "EMBED"
    ES_WRITE = "ES_WRITE"
    KAFKA_PUBLISH = "KAFKA_PUBLISH"


class KbIngestionStatus(StrEnum):
    """摄入流水状态"""

    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# SQLAlchemy ENUM 列类型
_kb_source_type = SAEnum(KbSourceType, name="kb_source_type", create_constraint=True, validate_strings=True)
_kb_doc_status = SAEnum(KbDocStatus, name="kb_doc_status", create_constraint=True, validate_strings=True)
_kb_approval_status = SAEnum(KbApprovalStatus, name="kb_approval_status", create_constraint=True, validate_strings=True)
_kb_approval_action = SAEnum(KbApprovalAction, name="kb_approval_action", create_constraint=True, validate_strings=True)
_kb_embed_status = SAEnum(KbEmbedStatus, name="kb_embed_status", create_constraint=True, validate_strings=True)
_kb_ingestion_stage = SAEnum(KbIngestionStage, name="kb_ingestion_stage", create_constraint=True, validate_strings=True)
_kb_ingestion_status = SAEnum(KbIngestionStatus, name="kb_ingestion_status", create_constraint=True, validate_strings=True)


def _uuid_v7() -> uuid_utils.UUID:
    """生成 UUID v7（时序排序）"""
    return uuid_utils.uuid7()


# ── KbDocument ──


class KbDocument(Base):
    """知识库文档元数据表"""

    __tablename__ = "kb_document"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), primary_key=True, default=_uuid_v7,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[KbSourceType] = mapped_column(_kb_source_type, nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, comment="MinIO object key")
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
        _kb_approval_status, nullable=False, default=KbApprovalStatus.DRAFT,
    )
    doc_group: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
        comment="文档组 ID: 同一逻辑文档的不同版本共享同一 doc_group",
    )
    is_current_version: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="是否为当前生效版本",
    )
    allowed_roles: Mapped[list | None] = mapped_column(
        JSON, nullable=True, default=list, comment="允许访问的角色列表",
    )
    regulatory_tags: Mapped[list | None] = mapped_column(
        JSON, nullable=True, default=list, comment="监管标签",
    )
    source_document_number: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="发文编号/制度编号",
    )
    last_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_review_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="下次复核截止日期",
    )

    # ── LLM 抽取结果 ──
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment="LLM 自动摘要")
    llm_keywords: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list, comment="LLM 抽取关键词")
    llm_entities: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list, comment="LLM 抽取实体")

    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, default="system")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now, server_default=text("now()"),
    )

    chunks: Mapped[list[KbChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan",
    )
    ingestion_logs: Mapped[list[KbIngestionLog]] = relationship(
        back_populates="document", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_kb_document_category", "category"),
        Index("ix_kb_document_status", "status"),
        Index("ix_kb_document_approval_status", "approval_status"),
        Index(
            "ix_kb_document_content_hash",
            "content_hash",
            postgresql_where=text("content_hash IS NOT NULL"),
        ),
        Index(
            "ix_kb_document_current_version",
            "doc_group",
            unique=True,
            postgresql_where=text("is_current_version = true AND is_deleted = false"),
        ),
    )


# ── 审批工作流 ──


class KbDocumentApproval(Base):
    """文档审批记录表（append-only）"""

    __tablename__ = "kb_document_approval"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), primary_key=True, default=_uuid_v7,
    )
    document_id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), ForeignKey("kb_document.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    action: Mapped[KbApprovalAction] = mapped_column(_kb_approval_action, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_kb_approval_document_created", "document_id", "created_at"),
    )


# ── KbChunk ──


class KbChunk(Base):
    """文档分块表

    embedding 列存储向量（LargeBinary，序列化为 float32 bytes），
    model_version 标记嵌入模型版本，支持影子索引 + 灰度切换。
    PG 是真相源，ES 可从 PG 重建（读正文 → 重灌 ES，不需重跑嵌入模型）。
    """

    __tablename__ = "kb_chunk"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), primary_key=True, default=_uuid_v7,
    )
    document_id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), ForeignKey("kb_document.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_status: Mapped[KbEmbedStatus] = mapped_column(
        _kb_embed_status, nullable=False, default=KbEmbedStatus.PENDING,
    )
    es_indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── 嵌入向量 + 版本治理 ──
    embedding: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True,
        comment="float32 向量序列化 bytes，ES 重建时可直接读取无需重跑模型",
    )
    model_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        comment="嵌入模型版本标识，如 bge-m3-v1，支持影子索引灰度切换",
    )

    # ── Parent-Child 分块字段 ──
    parent_chunk_id: Mapped[uuid_utils.UUID | None] = mapped_column(
        Uuid(native_uuid=False), ForeignKey("kb_chunk.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_type: Mapped[str] = mapped_column(String(16), nullable=False, default="plain_text")
    heading_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()"),
    )

    document: Mapped[KbDocument] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_kb_chunk_doc_index", "document_id", "chunk_index"),
        Index(
            "ix_kb_chunk_embedding_pending",
            "embedding_status",
            postgresql_where=text("embedding_status = 'PENDING'"),
        ),
        Index(
            "ix_kb_chunk_es_not_indexed",
            "es_indexed",
            postgresql_where=text("es_indexed = false"),
        ),
        Index("ix_kb_chunk_parent_chunk_id", "parent_chunk_id"),
        Index("ix_kb_chunk_model_version", "model_version"),
    )


# ── KbIngestionLog ──


class KbIngestionLog(Base):
    """文档摄入流水日志表"""

    __tablename__ = "kb_ingestion_log"

    id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), primary_key=True, default=_uuid_v7,
    )
    document_id: Mapped[uuid_utils.UUID] = mapped_column(
        Uuid(native_uuid=False), ForeignKey("kb_document.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    stage: Mapped[KbIngestionStage] = mapped_column(_kb_ingestion_stage, nullable=False)
    status: Mapped[KbIngestionStatus] = mapped_column(_kb_ingestion_status, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_detail: Mapped[dict | None] = mapped_column("step_detail_json", JSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.now, server_default=text("now()"),
    )

    document: Mapped[KbDocument] = relationship(back_populates="ingestion_logs")
