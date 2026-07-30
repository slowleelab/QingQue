"""add user_account table for RBAC

Revision ID: 006
Revises: 005
Create Date: 2026-07-26

Changes:
- New table: user_account (多用户 RBAC 基础，替代单 admin 密码环境变量方案)
  - 密码 PBKDF2-HMAC-SHA256 哈希存储
  - 角色与 JWT claim 对齐: customer / agent / admin / service
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_account",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="customer"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("display_name", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_user_account_username", "user_account", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_user_account_username", table_name="user_account")
    op.drop_table("user_account")
