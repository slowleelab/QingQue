"""add_parent_child_chunk_fields

Revision ID: 2221cd4b9c4b
Revises: 9a2930672730
Create Date: 2026-04-30 17:39:39.413890
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2221cd4b9c4b"
down_revision: str | None = "9a2930672730"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("kb_chunk", schema=None) as batch_op:
        batch_op.add_column(sa.Column("parent_chunk_id", sa.Uuid(native_uuid=False), nullable=True))
        batch_op.add_column(
            sa.Column("chunk_type", sa.String(length=16), nullable=False, server_default="plain_text")
        )
        batch_op.add_column(sa.Column("heading_path", sa.String(length=512), nullable=True))
        batch_op.create_index("ix_kb_chunk_parent_chunk_id", ["parent_chunk_id"], unique=False)
        batch_op.create_foreign_key(None, "kb_chunk", ["parent_chunk_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("kb_chunk", schema=None) as batch_op:
        batch_op.drop_constraint(None, type_="foreignkey")
        batch_op.drop_index("ix_kb_chunk_parent_chunk_id")
        batch_op.drop_column("heading_path")
        batch_op.drop_column("chunk_type")
        batch_op.drop_column("parent_chunk_id")
