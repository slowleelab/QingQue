"""merge 006 and decision_log branches

Revision ID: 5f63f057bc85
Revises: 006, c7d8e9f0a1b2
Create Date: 2026-08-07 12:40:26.550900
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision: str = '5f63f057bc85'
down_revision: Union[str, None] = ('006', 'c7d8e9f0a1b2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
