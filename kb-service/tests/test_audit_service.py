"""业务审计服务测试 (I1-C4)"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kb.security.audit_service import AuditService, _hash_query


def _principal(**overrides) -> SimpleNamespace:
    base = dict(
        actor_id="alice",
        actor_role="editor",
        tenant_id="default",
        roles=["editor"],
        auth_method="jwt",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestHashQuery:
    """query_hash 不存原文"""

    def test_hash_deterministic(self):
        h1 = _hash_query("信用卡额度")
        h2 = _hash_query("信用卡额度")
        assert h1 == h2

    def test_hash_md5_length(self):
        h = _hash_query("test")
        assert len(h) == 32
        assert h == hashlib.md5(b"test").hexdigest()

    def test_hash_differs_per_query(self):
        assert _hash_query("a") != _hash_query("b")

    def test_hash_unicode(self):
        # 中文 query 不能报错
        h = _hash_query("信用卡额度调整流程")
        assert len(h) == 32


class TestLogRetrieval:
    """log_retrieval 写 KbRetrievalAudit"""

    @pytest.mark.asyncio
    async def test_log_retrieval_adds_record(self):
        db = AsyncMock()
        db.add = MagicMock()

        audit = AuditService(db)
        principal = _principal()
        await audit.log_retrieval(
            principal=principal,
            query="信用卡",
            top_k=10,
            result_count=8,
            latency_ms=42,
            search_type="hybrid",
            degraded=False,
            request_id="req-1",
        )

        db.add.assert_called_once()
        record = db.add.call_args[0][0]
        assert record.actor_id == "alice"
        assert record.tenant_id == "default"
        assert record.top_k == 10
        assert record.result_count == 8
        assert record.latency_ms == 42
        assert record.search_type == "hybrid"
        assert record.degraded is False
        assert record.request_id == "req-1"
        # query 存 hash 不存原文
        assert record.query_hash == hashlib.md5("信用卡".encode()).hexdigest()
        assert audit.pending_count == 1

    @pytest.mark.asyncio
    async def test_log_retrieval_tenant_passthrough(self):
        """JWT 含 tenant_id 时正确写入"""
        db = AsyncMock()
        db.add = MagicMock()

        audit = AuditService(db)
        principal = _principal(actor_id="bob", tenant_id="bank-prod")
        await audit.log_retrieval(
            principal=principal,
            query="贷款",
            top_k=5,
            result_count=0,
            latency_ms=10,
        )

        record = db.add.call_args[0][0]
        assert record.tenant_id == "bank-prod"
        assert record.actor_id == "bob"

    @pytest.mark.asyncio
    async def test_log_retrieval_failure_does_not_raise(self):
        """落表失败不抛 (业务优先)"""
        db = AsyncMock()
        db.add = MagicMock(side_effect=Exception("DB 炸了"))

        audit = AuditService(db)
        principal = _principal()
        # 不能抛
        await audit.log_retrieval(
            principal=principal,
            query="x",
            top_k=1,
            result_count=0,
            latency_ms=0,
        )
        assert audit.pending_count == 0  # 失败不入账

    @pytest.mark.asyncio
    async def test_log_retrieval_degraded_flag(self):
        db = AsyncMock()
        db.add = MagicMock()
        audit = AuditService(db)
        await audit.log_retrieval(
            principal=_principal(),
            query="x",
            top_k=1,
            result_count=0,
            latency_ms=0,
            degraded=True,
        )
        record = db.add.call_args[0][0]
        assert record.degraded is True


class TestLog:
    """log 通用业务事件 (structlog 落日志, 不写 DB)"""

    @pytest.mark.asyncio
    async def test_log_emits_structlog_event(self, capsys):
        """通用事件走 structlog JSON 输出, 不会调 db.add"""
        from kb.logging import configure_logging
        configure_logging("INFO")

        db = AsyncMock()
        db.add = MagicMock()
        audit = AuditService(db)

        await audit.log(
            event_type="document.publish",
            principal=_principal(),
            resource="doc-123",
            action="PUBLISH",
            result="success",
            detail={"from_status": "APPROVED", "to_status": "PUBLISHED"},
            request_id="req-2",
            ip="10.0.0.1",
            ua="ops/1.0",
        )

        # 不写 DB
        db.add.assert_not_called()
        # structlog JSON 输出到 stdout, 含 event=business_audit
        captured = capsys.readouterr()
        assert "business_audit" in captured.out
        assert "document.publish" in captured.out
        assert "alice" in captured.out
        assert "doc-123" in captured.out


class TestPendingCount:
    """pending_count 用于业务端点判断是否需要 commit"""

    @pytest.mark.asyncio
    async def test_pending_count_increments(self):
        db = AsyncMock()
        db.add = MagicMock()
        audit = AuditService(db)

        assert audit.pending_count == 0
        await audit.log_retrieval(
            principal=_principal(), query="a", top_k=1, result_count=0, latency_ms=0,
        )
        assert audit.pending_count == 1
        await audit.log_retrieval(
            principal=_principal(), query="b", top_k=1, result_count=0, latency_ms=0,
        )
        assert audit.pending_count == 2
