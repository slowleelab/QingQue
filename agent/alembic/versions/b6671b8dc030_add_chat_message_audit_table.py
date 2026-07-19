"""add_chat_message_audit_table

Revision ID: b6671b8dc030
Revises: a918a6a3f1c8
Create Date: 2026-05-31 23:10:00.881030
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b6671b8dc030'
down_revision: Union[str, None] = 'a918a6a3f1c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENUM_SQL = "chat_message_status"
_ENUM_VALUES = "'QUEUED', 'PROCESSING', 'DONE', 'SKIPPED', 'ERROR'"


def upgrade() -> None:
    conn = op.get_bind()

    # 幂等检查
    from sqlalchemy import inspect
    inspector = inspect(conn)
    if 'chat_message' in inspector.get_table_names():
        return

    # 创建 ENUM 类型（幂等）
    conn.execute(sa.text(f"""
        DO $$ BEGIN
            CREATE TYPE {_ENUM_SQL} AS ENUM ({_ENUM_VALUES});
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """))

    # 原生 SQL 创建表（避免 SQLAlchemy ENUM listener 重复创建类型）
    op.execute(sa.text(f"""
        CREATE TABLE chat_message (
            id UUID PRIMARY KEY,
            session_id VARCHAR(64) NOT NULL,
            message_id VARCHAR(64) NOT NULL UNIQUE,
            customer_id VARCHAR(64),
            channel VARCHAR(16),
            content TEXT NOT NULL,
            quick_intent VARCHAR(32),
            intent VARCHAR(32),
            processing_status {_ENUM_SQL} NOT NULL,
            processing_duration_ms INTEGER,
            source VARCHAR(32),
            trace_id VARCHAR(64),
            error_message TEXT,
            metadata_json JSON,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))

    # 创建索引
    op.create_index('ix_chat_message_session', 'chat_message', ['session_id'])
    op.create_index('ix_chat_message_created', 'chat_message', ['created_at'])
    op.create_index('ix_chat_message_intent', 'chat_message', ['intent'])
    op.execute(sa.text(
        "CREATE INDEX ix_chat_message_content_fts ON chat_message "
        "USING gin (to_tsvector('simple', content))"
    ))


def downgrade() -> None:
    op.drop_index('ix_chat_message_content_fts', table_name='chat_message')
    op.drop_index('ix_chat_message_intent', table_name='chat_message')
    op.drop_index('ix_chat_message_created', table_name='chat_message')
    op.drop_index('ix_chat_message_session', table_name='chat_message')
    op.drop_table('chat_message')
    op.execute(sa.text(f"DROP TYPE IF EXISTS {_ENUM_SQL}"))
