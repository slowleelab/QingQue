"""add kb approval workflow and compliance fields

Revision ID: 003
Revises: 002_create_audit_log
Create Date: 2026-07-15

Changes:
- KbDocument: add approval_status, doc_group, is_current_version, allowed_roles,
  regulatory_tags, source_document_number, last_review_date, next_review_date
- Drop content_hash unique constraint (allow version coexistence)
- Add partial unique index on (doc_group WHERE is_current_version=true)
- New table: kb_document_approval (append-only audit trail)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, TIMESTAMP

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. KbDocument 新增字段
    op.add_column("kb_document", sa.Column("approval_status", sa.String(32), nullable=False, server_default="DRAFT"))
    op.add_column("kb_document", sa.Column("doc_group", sa.String(64), nullable=True))
    op.add_column("kb_document", sa.Column("is_current_version", sa.Boolean, nullable=False, server_default="true"))
    op.add_column("kb_document", sa.Column("allowed_roles", JSON, nullable=True))
    op.add_column("kb_document", sa.Column("regulatory_tags", JSON, nullable=True))
    op.add_column("kb_document", sa.Column("source_document_number", sa.String(128), nullable=True))
    op.add_column("kb_document", sa.Column("last_review_date", sa.Date, nullable=True))
    op.add_column("kb_document", sa.Column("next_review_date", sa.Date, nullable=True))

    # 2. 删除 content_hash 唯一索引，改为普通索引（支持版本共存）
    op.drop_index("ix_kb_document_content_hash", table_name="kb_document")
    op.create_index(
        "ix_kb_document_content_hash",
        "kb_document",
        ["content_hash"],
        postgresql_where=sa.text("content_hash IS NOT NULL"),
    )

    # 3. 新增索引
    op.create_index("ix_kb_document_approval_status", "kb_document", ["approval_status"])
    op.create_index("ix_kb_document_doc_group", "kb_document", ["doc_group"])
    op.create_index(
        "ix_kb_document_current_version",
        "kb_document",
        ["doc_group"],
        unique=True,
        postgresql_where=sa.text("is_current_version = true AND is_deleted = false"),
    )

    # 4. 将已有 COMPLETED 文档标记为 PUBLISHED（数据迁移）
    op.execute("UPDATE kb_document SET approval_status = 'PUBLISHED' WHERE status = 'COMPLETED'")

    # 5. 创建审批工作流表
    op.create_table(
        "kb_document_approval",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("document_id", sa.Uuid(native_uuid=False), sa.ForeignKey("kb_document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_approval_document", "kb_document_approval", ["document_id"])
    op.create_index("ix_kb_approval_document_created", "kb_document_approval", ["document_id", "created_at"])


def downgrade() -> None:
    op.drop_table("kb_document_approval")
    op.drop_index("ix_kb_document_current_version", table_name="kb_document")
    op.drop_index("ix_kb_document_doc_group", table_name="kb_document")
    op.drop_index("ix_kb_document_approval_status", table_name="kb_document")
    op.drop_index("ix_kb_document_content_hash", table_name="kb_document")
    op.create_index(
        "ix_kb_document_content_hash",
        "kb_document",
        ["content_hash"],
        unique=True,
        postgresql_where=sa.text("content_hash IS NOT NULL"),
    )
    op.drop_column("kb_document", "next_review_date")
    op.drop_column("kb_document", "last_review_date")
    op.drop_column("kb_document", "source_document_number")
    op.drop_column("kb_document", "regulatory_tags")
    op.drop_column("kb_document", "allowed_roles")
    op.drop_column("kb_document", "is_current_version")
    op.drop_column("kb_document", "doc_group")
    op.drop_column("kb_document", "approval_status")
