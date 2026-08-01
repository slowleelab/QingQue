"""Admin 端点 bug 修复 + 业务审计查询测试 (I1-C5)"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

fastapi = pytest.importorskip("fastapi")

from kb.orm.kb import KbApprovalAction, KbApprovalStatus  # noqa: E402


# ── Bug 修复: 字段名 + 缓存前缀 ──


class TestDiagnosticsFix:
    """diagnostics 不再 fallback (bug: KbIngestionLog.started_at → created_at)"""

    @pytest.mark.asyncio
    async def test_diagnostics_uses_created_at(self):
        """核心 SQL 不再引用不存在的 started_at"""
        import inspect
        import re
        from kb.api.admin import diagnostics

        src = inspect.getsource(diagnostics)
        # 排除 URL 路径里的 "started_at" (api/v1/diagnostics 误命中)
        # 只查 KbIngestionLog.started_at 字段引用
        bug_pattern = re.compile(r"KbIngestionLog\.started_at|\.started_at\.is_not")
        assert not bug_pattern.search(src), "diagnostics 还引用 KbIngestionLog.started_at, bug 未修"
        # 新实现: 按 created_at >= 7d_ago
        assert "created_at" in src
        assert "seven_days_ago" in src or "7 days" in src or "timedelta" in src


class TestClearCacheFix:
    """clear-cache 清的是真实缓存前缀 (bug: kb:retrieve:* → kp:rag:cache:*)"""

    @pytest.mark.asyncio
    async def test_clear_cache_uses_correct_prefix(self):
        import inspect
        from kb.api.admin import clear_cache

        src = inspect.getsource(clear_cache)
        assert "kb:retrieve:*" not in src, "clear-cache 还用错的旧前缀, bug 未修"
        assert "kp:rag:cache:*" in src, "clear-cache 没用真实缓存前缀"

    @pytest.mark.asyncio
    async def test_clear_cache_deletes_keys(self):
        """真实执行: 模拟 redis.scan_iter 返 [key1, key2, key3] → 删 3 条"""
        from kb.api.admin import clear_cache

        keys = ["kp:rag:cache:h:abc", "kp:rag:cache:b:def"]

        class _AsyncIter:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not keys:
                    raise StopAsyncIteration
                return keys.pop(0)

        redis = MagicMock()
        redis.scan_iter = MagicMock(return_value=_AsyncIter())
        redis.delete = AsyncMock()

        # patch get_redis 返回 mock
        with patch("kb.api.admin.get_redis", return_value=redis):
            result = await clear_cache(_api_key=None)

        assert result["deleted_keys"] == 2
        assert redis.delete.call_count == 2


class TestClearCacheNoRedis:
    @pytest.mark.asyncio
    async def test_clear_cache_503_when_redis_unavailable(self):
        from fastapi import HTTPException
        from kb.api.admin import clear_cache

        with patch("kb.api.admin.get_redis", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await clear_cache(_api_key=None)
        assert exc.value.status_code == 503


# ── 业务审计查询端点 ──


def _make_approval(**overrides) -> SimpleNamespace:
    base = dict(
        id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        action=KbApprovalAction.PUBLISH,
        from_status="APPROVED",
        to_status="PUBLISHED",
        actor_id="alice",
        actor_role="editor",
        comment="上线",
        ip="10.0.0.1",
        request_id="req-1",
        risk_level="normal",
        operation_result="success",
        tenant_id="default",
        created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_retrieval(**overrides) -> SimpleNamespace:
    base = dict(
        id="00000000-0000-0000-0000-000000000003",
        actor_id="alice",
        tenant_id="default",
        query_hash="abc123",
        top_k=10,
        result_count=8,
        latency_ms=42,
        search_type="hybrid",
        degraded=False,
        request_id="req-2",
        created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestBusinessAuditEndpoint:
    @pytest.mark.asyncio
    async def test_returns_both_approvals_and_retrievals(self):
        from kb.api.admin import business_audit

        approval = _make_approval()
        retrieval = _make_retrieval()

        db = AsyncMock()
        # 两次 execute 分别返 approvals + retrievals
        db.execute = AsyncMock(side_effect=[
            SimpleNamespace(scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=[approval])))),
            SimpleNamespace(scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=[retrieval])))),
        ])

        result = await business_audit(db=db, _api_key=None, doc_id=None, actor_id=None, limit=50)

        assert result["summary"]["approvals_returned"] == 1
        assert result["summary"]["retrievals_returned"] == 1
        assert len(result["approvals"]) == 1
        assert len(result["retrievals"]) == 1
        # 审批字段
        a = result["approvals"][0]
        assert a["actor_id"] == "alice"
        assert a["action"] == "PUBLISH"
        assert a["from_status"] == "APPROVED"
        assert a["to_status"] == "PUBLISHED"
        assert a["risk_level"] == "normal"
        # 检索字段 (含 query_hash, 无 query 原文)
        r = result["retrievals"][0]
        assert r["query_hash"] == "abc123"
        assert "query" not in r  # 不暴露原文
        assert r["degraded"] is False
        assert r["tenant_id"] == "default"

    @pytest.mark.asyncio
    async def test_filters_by_actor_id(self):
        from kb.api.admin import business_audit

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            SimpleNamespace(scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=[])))),
            SimpleNamespace(scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=[])))),
        ])

        await business_audit(db=db, _api_key=None, doc_id=None, actor_id="bob", limit=10)
        # 2 次 execute, 每次都用 actor_id
        assert db.execute.call_count == 2
        for call in db.execute.call_args_list:
            stmt = call.args[0]
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            assert "bob" in compiled

    @pytest.mark.asyncio
    async def test_invalid_doc_id_400(self):
        from fastapi import HTTPException
        from kb.api.admin import business_audit

        db = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await business_audit(db=db, _api_key=None, doc_id="not-a-uuid", actor_id=None, limit=10)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_limit_capped_at_200(self):
        from kb.api.admin import business_audit

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            SimpleNamespace(scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=[])))),
            SimpleNamespace(scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=[])))),
        ])

        await business_audit(db=db, _api_key=None, doc_id=None, actor_id=None, limit=10000)
        # limit 被钳到 200
        for call in db.execute.call_args_list:
            stmt = call.args[0]
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            assert "200" in compiled  # LIMIT 200

    @pytest.mark.asyncio
    async def test_no_pii_leaked(self):
        """retrievals 不含 query 原文, 只有 hash"""
        from kb.api.admin import business_audit

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            SimpleNamespace(scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=[])))),
            SimpleNamespace(scalars=MagicMock(return_value=SimpleNamespace(all=MagicMock(return_value=[])))),
        ])

        result = await business_audit(db=db, _api_key=None, doc_id=None, actor_id=None, limit=10)
        # 顶层不含 query 字段
        assert "query" not in str(result)
        for r in result["retrievals"]:
            assert "query" not in r or "query_hash" in r
