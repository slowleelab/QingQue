"""P0-2.3 publish 同步 ES 校验 + 重建 (7 用例)

覆盖:
- T21 publish 正常路径 (ES count == PG count) → 不重建
- T22 publish 前 ES count=0 → 触发 reindex 重建
- T23 publish 前 ES count 与 PG 不一致 → 触发 reindex
- T24 ES 不可用 (es=None) → publish 仍 200, 不阻塞, 留 warning log
- T25 publish doc 没有 KbChunk (pg_count=0) → publish 仍 200, 不 reindex
- T26 _build_reindex_metadata 包含 allowed_roles 从 PG 读
- T27 旧 inline metadata (reindex_document) 已用 _build_reindex_metadata 替代
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kb.api.approval import _ensure_es_in_sync
from kb.api.documents import _build_reindex_metadata
from kb.orm.kb import KbApprovalStatus, KbDocument


# ── helper: 构造 fake KbDocument + db + es ──


def _make_doc(
    *,
    doc_id: str | None = None,
    tenant_id: str = "default",
    allowed_roles: list | None = None,
    approval_status: str = "PUBLISHED",
) -> MagicMock:
    if doc_id is None:
        doc_id = str(uuid.uuid4())
    doc = MagicMock()
    doc.id = doc_id
    doc.tenant_id = tenant_id
    doc.allowed_roles = allowed_roles if allowed_roles is not None else []
    doc.approval_status = MagicMock()
    doc.approval_status.value = approval_status
    doc.is_current_version = True
    doc.doc_group = None
    doc.category = "policy"
    doc.doc_type = "pdf"
    doc.card_type = "gold"
    doc.customer_tier = "vip"
    doc.security_level = "internal"
    doc.version = "1.0"
    doc.llm_keywords = ["k1"]
    return doc


def _make_db(doc: MagicMock, chunks: list[MagicMock]) -> MagicMock:
    db = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    async def fake_get(_model, pk):
        if str(pk) == str(doc.id):
            return doc
        return None

    db.get = fake_get

    # count(KbChunk) → scalar(pg_count)
    async def fake_execute(stmt):
        # 简化: 总是返回 chunks 长度
        return MagicMock(scalar=MagicMock(return_value=len(chunks)))

    db.execute = fake_execute
    return db


def _make_es(es_count: int = 0) -> MagicMock:
    es = MagicMock()
    es.count = AsyncMock(return_value={"count": es_count})
    es.index = AsyncMock()
    es.delete_by_query = AsyncMock(return_value={"deleted": es_count})
    es.indices = MagicMock()
    es.indices.create = AsyncMock()
    es.indices.exists = AsyncMock(return_value=True)
    return es


# ═══════════════════════════════════════════════════════════════════════
# T21 正常路径
# ═══════════════════════════════════════════════════════════════════════


class TestPublishESNormal:
    @pytest.mark.asyncio
    async def test_publish_consistent_no_reindex(self):
        """ES count == PG count → 不重建, 返回 reindexed=False"""
        doc = _make_doc()
        chunks = [MagicMock(id=str(uuid.uuid4()), content="c", chunk_type="plain", heading_path=None,
                            embedding=None, model_version="v1") for _ in range(3)]
        db = _make_db(doc, chunks)
        es = _make_es(es_count=3)  # 一致

        # 用真 AsyncElasticsearch 走 if 分支: 但 es 是 MagicMock 不是真 ES
        # → _ensure_es_in_sync 走 'es_unavailable' 分支, 返回 skipped
        # 调整: 改用符合 isinstance(es, AsyncElasticsearch) 的真客户端
        from elasticsearch import AsyncElasticsearch
        real_es = MagicMock(spec=AsyncElasticsearch)
        real_es.count = AsyncMock(return_value={"count": 3})
        real_es.delete_by_query = AsyncMock(return_value={"deleted": 3})
        real_es.index = AsyncMock()
        real_es.options = MagicMock(return_value=real_es)

        result = await _ensure_es_in_sync(str(doc.id), db, real_es)
        assert result["es_count"] == 3
        assert result["pg_count"] == 3
        assert result["reindexed"] is False
        assert result["added"] == 0
        assert result["skipped"] is None


# ═══════════════════════════════════════════════════════════════════════
# T22 ES count=0 → 重建
# ═══════════════════════════════════════════════════════════════════════


class TestPublishESEmpty:
    @pytest.mark.asyncio
    async def test_publish_es_zero_reindexes(self):
        """ES count=0 → 触发 reindex 重建, 返回 reindexed=True"""
        from elasticsearch import AsyncElasticsearch

        doc = _make_doc()
        chunks = [MagicMock(id=str(uuid.uuid4()), content="c", chunk_type="plain",
                            heading_path="h1 > h2", embedding=b"\x00", model_version="v1") for _ in range(2)]

        # mark_es_indexed 调 db.execute UPDATE, 简化: 不报错就行
        db = _make_db(doc, chunks)

        with pytest.MonkeyPatch.context() as mp:
            # _ensure_es_in_sync 在函数内 import kb.pipeline.writer, 必须在源模块上 patch
            mp.setattr("kb.pipeline.writer.deserialize_embedding", lambda b: [])
            mp.setattr("kb.pipeline.writer.write_chunks_to_es",
                       AsyncMock(return_value=len(chunks)))
            mp.setattr("kb.pipeline.writer.delete_chunks_from_es", AsyncMock(return_value=0))
            mp.setattr("kb.pipeline.writer.mark_es_indexed", AsyncMock(return_value=len(chunks)))
            real_es = MagicMock(spec=AsyncElasticsearch)
            real_es.count = AsyncMock(return_value={"count": 0})
            real_es.delete_by_query = AsyncMock(return_value={"deleted": 0})
            real_es.index = AsyncMock()
            real_es.options = MagicMock(return_value=real_es)

            result = await _ensure_es_in_sync(str(doc.id), db, real_es)

        assert result["es_count"] == 0
        assert result["pg_count"] == 2
        assert result["reindexed"] is True
        assert result["added"] == 2
        assert result["skipped"] is None


# ═══════════════════════════════════════════════════════════════════════
# T23 ES count 不一致 → 重建
# ═══════════════════════════════════════════════════════════════════════


class TestPublishESMismatch:
    @pytest.mark.asyncio
    async def test_publish_mismatch_triggers_reindex(self):
        """ES count=1, PG count=5 → 不一致, 重建"""
        from elasticsearch import AsyncElasticsearch

        doc = _make_doc()
        chunks = [MagicMock(id=str(uuid.uuid4()), content="c", chunk_type="plain",
                            heading_path=None, embedding=None, model_version="v1") for _ in range(5)]
        db = _make_db(doc, chunks)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("kb.pipeline.writer.write_chunks_to_es",
                       AsyncMock(return_value=5))
            mp.setattr("kb.pipeline.writer.delete_chunks_from_es", AsyncMock(return_value=1))
            mp.setattr("kb.pipeline.writer.mark_es_indexed", AsyncMock(return_value=5))
            real_es = MagicMock(spec=AsyncElasticsearch)
            real_es.count = AsyncMock(return_value={"count": 1})
            real_es.delete_by_query = AsyncMock(return_value={"deleted": 1})
            real_es.index = AsyncMock()
            real_es.options = MagicMock(return_value=real_es)

            result = await _ensure_es_in_sync(str(doc.id), db, real_es)

        assert result["es_count"] == 1
        assert result["pg_count"] == 5
        assert result["reindexed"] is True
        assert result["added"] == 5


# ═══════════════════════════════════════════════════════════════════════
# T24 ES 不可用 (es=None) → 200, 留 warning
# ═══════════════════════════════════════════════════════════════════════


class TestPublishESUnavailable:
    @pytest.mark.asyncio
    async def test_publish_es_none_skips(self):
        """es=None → 跳过同步, 不阻塞 publish 业务, 返回 skipped=es_unavailable"""
        doc = _make_doc()
        chunks = [MagicMock(id=str(uuid.uuid4())) for _ in range(3)]
        db = _make_db(doc, chunks)

        result = await _ensure_es_in_sync(str(doc.id), db, es=None)

        assert result["reindexed"] is False
        assert result["skipped"] == "es_unavailable"

    @pytest.mark.asyncio
    async def test_publish_es_magicmock_skips(self):
        """es 是普通 MagicMock (非 AsyncElasticsearch spec) → 跳过同步"""
        doc = _make_doc()
        chunks = [MagicMock(id=str(uuid.uuid4())) for _ in range(3)]
        db = _make_db(doc, chunks)
        fake_es = MagicMock()  # 没 spec=AsyncElasticsearch

        result = await _ensure_es_in_sync(str(doc.id), db, fake_es)
        assert result["skipped"] == "es_unavailable"


# ═══════════════════════════════════════════════════════════════════════
# T25 publish doc 没有 KbChunk → publish 仍 200, 不 reindex
# ═══════════════════════════════════════════════════════════════════════


class TestPublishNoChunks:
    @pytest.mark.asyncio
    async def test_publish_no_chunks_skips(self):
        """pg_count=0 → 跳过同步, skipped=no_chunks"""
        from elasticsearch import AsyncElasticsearch

        doc = _make_doc()
        db = _make_db(doc, [])  # 无 chunks
        real_es = MagicMock(spec=AsyncElasticsearch)
        real_es.count = AsyncMock(return_value={"count": 0})

        result = await _ensure_es_in_sync(str(doc.id), db, real_es)
        assert result["reindexed"] is False
        assert result["skipped"] == "no_chunks"
        assert result["pg_count"] == 0


# ═══════════════════════════════════════════════════════════════════════
# T26 _build_reindex_metadata 包含 allowed_roles
# ═══════════════════════════════════════════════════════════════════════


class TestBuildReindexMetadata:
    def test_metadata_includes_allowed_roles(self):
        """allowed_roles 从 doc 读, 不接受请求体"""
        doc = _make_doc(allowed_roles=["admin", "reviewer"])
        metadata = _build_reindex_metadata(doc, doc_id_fallback="abc")
        assert metadata["allowed_roles"] == ["admin", "reviewer"]

    def test_metadata_default_empty_roles(self):
        """allowed_roles 缺省/空 → [] (全员可见, P0-1 一致)"""
        doc = _make_doc(allowed_roles=None)
        metadata = _build_reindex_metadata(doc)
        assert metadata["allowed_roles"] == []

    def test_metadata_tenant_id_from_doc(self):
        """tenant_id 强制从 doc.tenant_id, 防止伪造"""
        doc = _make_doc(tenant_id="bank-prod")
        metadata = _build_reindex_metadata(doc)
        assert metadata["tenant_id"] == "bank-prod"

    def test_metadata_approval_status_value(self):
        """approval_status 取 .value (字符串)"""
        doc = _make_doc(approval_status="PUBLISHED")
        metadata = _build_reindex_metadata(doc)
        assert metadata["approval_status"] == "PUBLISHED"

    def test_metadata_doc_group_fallback(self):
        """doc_group 缺省时用 doc_id_fallback"""
        doc = _make_doc()
        doc.doc_group = None
        metadata = _build_reindex_metadata(doc, doc_id_fallback="fb-123")
        assert metadata["doc_group"] == "fb-123"

    def test_metadata_uses_doc_group_when_present(self):
        """doc_group 已设置时优先用 doc_group"""
        doc = _make_doc()
        doc.doc_group = "group-1"
        metadata = _build_reindex_metadata(doc, doc_id_fallback="fb-123")
        assert metadata["doc_group"] == "group-1"


# ═══════════════════════════════════════════════════════════════════════
# T27 旧 inline metadata (reindex_document) 已用 _build_reindex_metadata 替代
# ═══════════════════════════════════════════════════════════════════════


class TestReindexDocumentUsesHelper:
    def test_reindex_uses_helper(self):
        """代码层验证: reindex_document 内部已用 _build_reindex_metadata

        这个测试通过 inspect 模块验证源码, 防止以后有人重新引入 inline metadata
        """
        import inspect

        from kb.api import documents as docs_module

        source = inspect.getsource(docs_module.reindex_document)
        # 必须有调用 _build_reindex_metadata
        assert "_build_reindex_metadata" in source, (
            "reindex_document 必须复用 _build_reindex_metadata 防止 metadata 漂移"
        )
        # 不能再有 "tenant_id\": doc.tenant_id 之类的 inline 字段 (P0-2.3 之前的形式)
        # 注意: 仍可能有 'doc.doc_group' 之类的引用 (从 helper 内部), 但 inline 构造已删除
        assert '"tenant_id":' not in source, (
            "reindex_document 不应有 inline tenant_id 字段, 必须用 helper"
        )
