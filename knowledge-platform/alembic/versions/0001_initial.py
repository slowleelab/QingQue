"""初始迁移 — 创建知识库全部表

Revision ID: 0001
Revises:
Create Date: 2026-07-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # kb_document
    op.create_table(
        "kb_document",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("source_type", sa.Enum("PDF", "DOCX", "HTML", "MARKDOWN", "TXT", "XLSX", name="kb_source_type"), nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("card_type", sa.String(32), nullable=True),
        sa.Column("customer_tier", sa.String(32), nullable=True),
        sa.Column("security_level", sa.String(16), nullable=False, server_default="internal"),
        sa.Column("version", sa.String(16), nullable=False, server_default="1.0"),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.Enum("PENDING", "KAFKA_QUEUED", "PROCESSING", "COMPLETED", "FAILED", "ARCHIVED", name="kb_doc_status"), nullable=False, server_default="PENDING"),
        sa.Column("approval_status", sa.Enum("DRAFT", "IN_REVIEW", "APPROVED", "PUBLISHED", "SUPERSEDED", "REJECTED", "ARCHIVED", name="kb_approval_status"), nullable=False, server_default="DRAFT"),
        sa.Column("doc_group", sa.String(64), nullable=True),
        sa.Column("is_current_version", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allowed_roles", postgresql.JSON(), nullable=True),
        sa.Column("regulatory_tags", postgresql.JSON(), nullable=True),
        sa.Column("source_document_number", sa.String(128), nullable=True),
        sa.Column("last_review_date", sa.Date(), nullable=True),
        sa.Column("next_review_date", sa.Date(), nullable=True),
        sa.Column("llm_summary", sa.Text(), nullable=True),
        sa.Column("llm_keywords", postgresql.JSON(), nullable=True),
        sa.Column("llm_entities", postgresql.JSON(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_document_category", "kb_document", ["category"])
    op.create_index("ix_kb_document_status", "kb_document", ["status"])
    op.create_index("ix_kb_document_approval_status", "kb_document", ["approval_status"])
    op.create_index("ix_kb_document_doc_group", "kb_document", ["doc_group"])
    op.create_index(
        "ix_kb_document_current_version",
        "kb_document",
        ["doc_group"],
        unique=True,
        postgresql_where=sa.text("is_current_version = true AND is_deleted = false"),
    )

    # kb_document_approval
    op.create_table(
        "kb_document_approval",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("document_id", sa.Uuid(native_uuid=False), sa.ForeignKey("kb_document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.Enum("CREATE", "SUBMIT", "APPROVE", "REJECT", "PUBLISH", "SUPERSEDE", "ARCHIVE", name="kb_approval_action"), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_approval_document_id", "kb_document_approval", ["document_id"])
    op.create_index("ix_kb_approval_document_created", "kb_document_approval", ["document_id", "created_at"])

    # kb_chunk
    op.create_table(
        "kb_chunk",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("document_id", sa.Uuid(native_uuid=False), sa.ForeignKey("kb_document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("embedding_status", sa.Enum("PENDING", "COMPLETED", "FAILED", name="kb_embed_status"), nullable=False, server_default="PENDING"),
        sa.Column("es_indexed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("model_version", sa.String(64), nullable=True),
        sa.Column("parent_chunk_id", sa.Uuid(native_uuid=False), sa.ForeignKey("kb_chunk.id", ondelete="SET NULL"), nullable=True),
        sa.Column("chunk_type", sa.String(16), nullable=False, server_default="plain_text"),
        sa.Column("heading_path", sa.String(512), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_chunk_document_id", "kb_chunk", ["document_id"])
    op.create_index("ix_kb_chunk_doc_index", "kb_chunk", ["document_id", "chunk_index"])
    op.create_index("ix_kb_chunk_parent_chunk_id", "kb_chunk", ["parent_chunk_id"])
    op.create_index("ix_kb_chunk_model_version", "kb_chunk", ["model_version"])

    # kb_ingestion_log
    op.create_table(
        "kb_ingestion_log",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("document_id", sa.Uuid(native_uuid=False), sa.ForeignKey("kb_document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.Enum("PARSE", "CLEAN", "EXTRACT", "CHUNK", "EMBED", "ES_WRITE", "KAFKA_PUBLISH", name="kb_ingestion_stage"), nullable=False),
        sa.Column("status", sa.Enum("RUNNING", "SUCCESS", "FAILED", "SKIPPED", name="kb_ingestion_status"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("step_detail_json", postgresql.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_ingestion_log_document_id", "kb_ingestion_log", ["document_id"])


def downgrade() -> None:
    op.drop_table("kb_ingestion_log")
    op.drop_table("kb_chunk")
    op.drop_table("kb_document_approval")
    op.drop_table("kb_document")
