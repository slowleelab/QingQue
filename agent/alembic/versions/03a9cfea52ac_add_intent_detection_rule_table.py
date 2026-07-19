"""add_intent_detection_rule_table

Revision ID: 03a9cfea52ac
Revises: b6671b8dc030
Create Date: 2026-06-01 23:30:43.314531
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '03a9cfea52ac'
down_revision: Union[str, None] = 'b6671b8dc030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect
    inspector = inspect(conn)
    if 'intent_detection_rule' in inspector.get_table_names():
        return

    op.create_table('intent_detection_rule',
        sa.Column('id', sa.Uuid(native_uuid=False), nullable=False),
        sa.Column('rule_id', sa.String(length=64), nullable=False),
        sa.Column('domain', sa.String(length=32), nullable=False),
        sa.Column('patterns', sa.JSON(), nullable=False),
        sa.Column('keywords', sa.JSON(), nullable=False),
        sa.Column('negation_of', sa.String(length=32), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('intent_detection_rule', schema=None) as batch_op:
        batch_op.create_index('ix_intent_detection_rule_rule_id', ['rule_id'], unique=True)
        batch_op.create_index('ix_intent_rule_domain_status', ['domain', 'status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('intent_detection_rule', schema=None) as batch_op:
        batch_op.drop_index('ix_intent_rule_domain_status')
        batch_op.drop_index('ix_intent_detection_rule_rule_id')
    op.drop_table('intent_detection_rule')
