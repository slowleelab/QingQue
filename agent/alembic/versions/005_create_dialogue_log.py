"""add dialogue_log table

Revision ID: 005
Revises: 004_create_faq_tables
Create Date: 2026-07-16

Changes:
- New table: dialogue_log (持久化对话记录，满足银行合规审计 5-7 年保存要求)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, TIMESTAMP

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dialogue_log",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("speaker", sa.String(16), nullable=False, comment="customer/agent/bot"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("intent", sa.String(32), nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("entities", JSON, nullable=True),
        sa.Column("response_source", sa.String(32), nullable=True),
        sa.Column("retrieval_context", sa.Text, nullable=True),
        sa.Column("emotion_label", sa.String(16), nullable=True),
        sa.Column("emotion_score", sa.Float, nullable=True),
        sa.Column("timestamp", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("customer_id", sa.String(64), nullable=True),
        sa.Column("channel_type", sa.String(16), nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_dialogue_log_session", "dialogue_log", ["session_id"])
    op.create_index("ix_dialogue_log_session_ts", "dialogue_log", ["session_id", "timestamp"])
    op.create_index("ix_dialogue_log_customer", "dialogue_log", ["customer_id"])
    op.create_index("ix_dialogue_log_speaker", "dialogue_log", ["speaker"])


def downgrade() -> None:
    op.drop_index("ix_dialogue_log_speaker", table_name="dialogue_log")
    op.drop_index("ix_dialogue_log_customer", table_name="dialogue_log")
    op.drop_index("ix_dialogue_log_session_ts", table_name="dialogue_log")
    op.drop_index("ix_dialogue_log_session", table_name="dialogue_log")
    op.drop_table("dialogue_log")
