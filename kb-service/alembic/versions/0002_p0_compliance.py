"""P0 合规迁移 — 多租户 / PII / 业务审计扩展

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-29

变更:
- kb_document: 新增 tenant_id, is_pii, redacted (多租户 + PII 治理)
- kb_chunk: 新增 tenant_id (避免 JOIN)
- kb_document_approval: 新增 tenant_id, ip, ua, request_id, operation_result, risk_level, retention_until (业务审计)
- 新建 kb_retrieval_audit (检索审计 append-only)
- 老数据回填 tenant_id='default'
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. kb_document: 多租户 + PII 治理 ──
    op.add_column(
        "kb_document",
        sa.Column("tenant_id", sa.String(32), nullable=False, server_default="default"),
    )
    op.add_column(
        "kb_document",
        sa.Column("is_pii", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "kb_document",
        sa.Column("redacted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_kb_document_tenant_id", "kb_document", ["tenant_id"])
    op.create_index(
        "ix_kb_document_is_pii",
        "kb_document",
        ["is_pii"],
        postgresql_where=sa.text("is_pii = true"),
    )

    # ── 2. kb_chunk: tenant_id 冗余 (避免跨表 JOIN) ──
    op.add_column(
        "kb_chunk",
        sa.Column("tenant_id", sa.String(32), nullable=True),
    )
    # 回填 chunk.tenant_id (从所属文档同步)
    op.execute(
        """
        UPDATE kb_chunk c
        SET tenant_id = d.tenant_id
        FROM kb_document d
        WHERE c.document_id = d.id
        """
    )
    # 回填后改为 NOT NULL
    op.alter_column("kb_chunk", "tenant_id", nullable=False, server_default="default")
    op.create_index("ix_kb_chunk_tenant_id", "kb_chunk", ["tenant_id"])

    # ── 3. kb_document_approval: 业务审计扩展字段 ──
    op.add_column(
        "kb_document_approval",
        sa.Column("tenant_id", sa.String(32), nullable=False, server_default="default"),
    )
    op.add_column(
        "kb_document_approval",
        sa.Column("ip", sa.String(45), nullable=True),  # IPv6 max 45 chars
    )
    op.add_column(
        "kb_document_approval",
        sa.Column("ua", sa.String(256), nullable=True),
    )
    op.add_column(
        "kb_document_approval",
        sa.Column("request_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "kb_document_approval",
        sa.Column(
            "operation_result",
            sa.String(16),
            nullable=False,
            server_default="success",
        ),
    )
    op.add_column(
        "kb_document_approval",
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="normal"),
    )
    op.add_column(
        "kb_document_approval",
        sa.Column(
            "retention_until",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    # 留存 5 年 (按 GB/T 22239 等保 2.0 三级要求)
    op.execute(
        """
        UPDATE kb_document_approval
        SET retention_until = created_at + interval '5 years'
        WHERE retention_until IS NULL
        """
    )
    op.create_index(
        "ix_kb_approval_tenant_id", "kb_document_approval", ["tenant_id"]
    )
    op.create_index(
        "ix_kb_approval_actor_id", "kb_document_approval", ["actor_id"]
    )

    # ── 4. 新表 kb_retrieval_audit (检索审计, append-only) ──
    op.create_table(
        "kb_retrieval_audit",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(32), nullable=False, server_default="default"),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("search_type", sa.String(16), nullable=True),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kb_retrieval_audit_actor", "kb_retrieval_audit", ["actor_id"])
    op.create_index("ix_kb_retrieval_audit_tenant", "kb_retrieval_audit", ["tenant_id"])
    op.create_index(
        "ix_kb_retrieval_audit_created", "kb_retrieval_audit", ["created_at"]
    )


def downgrade() -> None:
    # 顺序与 upgrade 相反
    op.drop_index("ix_kb_retrieval_audit_created", table_name="kb_retrieval_audit")
    op.drop_index("ix_kb_retrieval_audit_tenant", table_name="kb_retrieval_audit")
    op.drop_index("ix_kb_retrieval_audit_actor", table_name="kb_retrieval_audit")
    op.drop_table("kb_retrieval_audit")

    op.drop_index("ix_kb_approval_actor_id", table_name="kb_document_approval")
    op.drop_index("ix_kb_approval_tenant_id", table_name="kb_document_approval")
    op.drop_column("kb_document_approval", "retention_until")
    op.drop_column("kb_document_approval", "risk_level")
    op.drop_column("kb_document_approval", "operation_result")
    op.drop_column("kb_document_approval", "request_id")
    op.drop_column("kb_document_approval", "ua")
    op.drop_column("kb_document_approval", "ip")
    op.drop_column("kb_document_approval", "tenant_id")

    op.drop_index("ix_kb_chunk_tenant_id", table_name="kb_chunk")
    op.drop_column("kb_chunk", "tenant_id")

    op.drop_index("ix_kb_document_is_pii", table_name="kb_document")
    op.drop_index("ix_kb_document_tenant_id", table_name="kb_document")
    op.drop_column("kb_document", "redacted")
    op.drop_column("kb_document", "is_pii")
    op.drop_column("kb_document", "tenant_id")
