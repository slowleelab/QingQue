"""add faq tables

Revision ID: 004
Revises: 003_kb_approval_workflow
Create Date: 2026-07-15

Changes:
- New table: kb_faq (structured FAQ Q&A pairs)
- New table: kb_faq_search_log (search analytics)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, TIMESTAMP

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # kb_faq
    op.create_table(
        "kb_faq",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("question", sa.String(512), nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("variant_questions", JSON, nullable=False, server_default="[]"),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("card_types", JSON, nullable=False, server_default="[]"),
        sa.Column("customer_tiers", JSON, nullable=False, server_default="[]"),
        sa.Column("keywords", JSON, nullable=False, server_default="[]"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("doc_group", sa.String(64), nullable=True),
        sa.Column("version", sa.String(16), nullable=False, server_default="1.0"),
        sa.Column("approval_status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("is_current_version", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("effective_date", sa.Date, nullable=True),
        sa.Column("expiry_date", sa.Date, nullable=True),
        sa.Column("allowed_roles", JSON, nullable=True),
        sa.Column("regulatory_tags", JSON, nullable=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_faq_category", "kb_faq", ["category"])
    op.create_index("ix_kb_faq_approval_status", "kb_faq", ["approval_status"])
    op.create_index("ix_kb_fqa_published", "kb_faq", ["approval_status", "is_current_version", "is_deleted"])
    op.create_index(
        "ix_kb_faq_current_version", "kb_faq", ["doc_group"],
        unique=True, postgresql_where=sa.text("is_current_version = true AND is_deleted = false"),
    )

    # kb_faq_search_log
    op.create_table(
        "kb_faq_search_log",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("query", sa.String(512), nullable=False),
        sa.Column("match_type", sa.String(16), nullable=False),
        sa.Column("faq_id", sa.Uuid(native_uuid=False), nullable=True),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("user_role", sa.String(32), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_faq_log_created", "kb_faq_search_log", ["created_at"])
    op.create_index("ix_kb_faq_log_faq", "kb_faq_search_log", ["faq_id"])
    op.create_index("ix_kb_faq_log_match_type", "kb_faq_search_log", ["match_type"])


def downgrade() -> None:
    op.drop_table("kb_faq_search_log")
    op.drop_table("kb_faq")
