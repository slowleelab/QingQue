"""知识库 ORM 模型

定义知识库核心数据表：
- kb_document: 知识库文档元数据
- kb_chunk: 文档分块
- kb_ingestion_log: 文档摄入流水日志

使用 SQLAlchemy 2.0 声明式映射，UUID v7 作为主键。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum as PyEnum

import uuid_utils
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
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


class KbSourceType(str, PyEnum):
    """文档来源类型"""

    PDF = "PDF"
    DOCX = "DOCX"
    HTML = "HTML"
    MARKDOWN = "MARKDOWN"
    TXT = "TXT"
    XLSX = "XLSX"


class KbDocStatus(str, PyEnum):
    """文档处理状态"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"
    KAFKA_PENDING = "KAFKA_PENDING"


class KbEmbedStatus(str, PyEnum):
    """嵌入状态"""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class KbIngestionStage(str, PyEnum):
    """摄入流水阶段"""

    PARSE = "PARSE"
    CLEAN = "CLEAN"
    CHUNK = "CHUNK"
    EMBED = "EMBED"
    ES_WRITE = "ES_WRITE"
    MILVUS_WRITE = "MILVUS_WRITE"
    KAFKA_PUBLISH = "KAFKA_PUBLISH"


class KbIngestionStatus(str, PyEnum):
    """摄入流水状态"""

    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


# SQLAlchemy ENUM 列类型（映射到 PG ENUM）
_kb_source_type = SAEnum(KbSourceType, name="kb_source_type", create_constraint=True, validate_strings=True)
_kb_doc_status = SAEnum(KbDocStatus, name="kb_doc_status", create_constraint=True, validate_strings=True)
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
        Index(
            "ix_kb_document_content_hash",
            "content_hash",
            unique=True,
            postgresql_where=text("content_hash IS NOT NULL"),
        ),
    )


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


class ScriptStatus(str, PyEnum):
    """话术模板状态"""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


_script_status = SAEnum(ScriptStatus, name="script_status", create_constraint=True, validate_strings=True)


# ── ScriptTemplate ──


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
    variables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
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


# ── ScriptUsageLog ──


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
