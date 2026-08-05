"""add_decision_log_table

E2 可解释性 / 决策可追溯:
记录每个 agent 决策 (action + reasoning + evidence + latency),
用于客户查询"AI 怎么回答的" + 监管审计批量导出.

Revision ID: c7d8e9f0a1b2
Revises: b6671b8dc030
Create Date: 2026-08-04 12:00:00.000000

D2 GDPR 删除: customer_id 索引用于批量删除 + decision_id 索引用于单条回溯.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "b6671b8dc030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # 幂等检查
    from sqlalchemy import inspect

    inspector = inspect(conn)
    if "decision_log" in inspector.get_table_names():
        return

    # 原生 SQL 创建表 (与其他迁移风格一致, 避免 SQLAlchemy ENUM listener 副作用)
    op.execute(
        sa.text("""
        CREATE TABLE decision_log (
            id UUID PRIMARY KEY,
            decision_id VARCHAR(64) NOT NULL UNIQUE,
            session_id VARCHAR(64) NOT NULL,
            turn_id VARCHAR(64) NOT NULL,
            customer_id VARCHAR(64),
            agent_name VARCHAR(64) NOT NULL,
            action VARCHAR(32) NOT NULL,
            reasoning TEXT NOT NULL,
            evidence_json JSON,
            latency_ms REAL NOT NULL DEFAULT 0.0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    )

    # GDPR 删除 / 客户查询场景: 客户级查询 + 倒序时间扫描
    op.create_index("ix_decision_log_customer_created", "decision_log", ["customer_id", "created_at"])
    # 会话级查询 (单次会话决策回放)
    op.create_index("ix_decision_log_session", "decision_log", ["session_id"])
    # 监管审计: 按 action + 时间范围扫描
    op.create_index("ix_decision_log_action_created", "decision_log", ["action", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_decision_log_action_created", table_name="decision_log")
    op.drop_index("ix_decision_log_session", table_name="decision_log")
    op.drop_index("ix_decision_log_customer_created", table_name="decision_log")
    op.drop_table("decision_log")
