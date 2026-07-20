"""create audit log table

Revision ID: 002
Revises: 03a9cfea52ac（接在 hash 系迁移链头部，原假设的 001 初始迁移不存在）
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, TIMESTAMP

revision = "002"
down_revision = "03a9cfea52ac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("timestamp", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(256), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("detail", JSON, nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])
    op.create_index("ix_audit_log_actor", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_target", "audit_log", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
