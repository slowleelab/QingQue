"""知识库文档删除端点测试

覆盖 B4 修复：DELETE /kb/documents/{doc_id} 此前只软删 DB，未清理 ES 索引与
Milvus 向量，导致已删文档仍被检索到。这里直接对端点函数做单元测试，验证
软删 DB 的同时会同步清理 ES(delete_by_query) 与 Milvus(delete expr)。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from smartcs.services.bot.router import delete_document


def _make_db(doc: object) -> MagicMock:
    """构造返回指定文档的 mock DbSession"""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=doc)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    return db


def _make_doc(doc_id: str = "doc-1") -> MagicMock:
    doc = MagicMock()
    doc.id = doc_id
    doc.is_deleted = False
    doc.deleted_at = None
    return doc


class TestDeleteDocument:
    """KB 文档删除端点测试"""

    @pytest.mark.asyncio
    async def test_soft_delete_marks_db(self) -> None:
        """软删除：DB 标记 is_deleted + deleted_at"""
        doc = _make_doc()
        db = _make_db(doc)
        await delete_document(doc_id="doc-1", db=db, es_client=None, milvus_collection=None)
        assert doc.is_deleted is True
        assert doc.deleted_at is not None
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleans_es_index(self) -> None:
        """同步调用 ES delete_by_query 按 doc_id 清理索引"""
        doc = _make_doc()
        db = _make_db(doc)
        es = MagicMock()
        es.delete_by_query = AsyncMock()

        await delete_document(doc_id="doc-1", db=db, es_client=es, milvus_collection=None)

        es.delete_by_query.assert_awaited_once()
        kwargs = es.delete_by_query.await_args.kwargs
        assert kwargs["body"]["query"]["term"]["doc_id"] == "doc-1"

    @pytest.mark.asyncio
    async def test_cleans_milvus_vectors(self) -> None:
        """同步调用 Milvus delete 按 doc_id 表达式清理向量"""
        doc = _make_doc()
        db = _make_db(doc)
        milvus = MagicMock()
        milvus.delete = MagicMock()

        await delete_document(doc_id="doc-1", db=db, es_client=None, milvus_collection=milvus)

        milvus.delete.assert_called_once()
        expr = milvus.delete.call_args.kwargs["expr"]
        assert 'doc_id == "doc-1"' in expr

    @pytest.mark.asyncio
    async def test_es_failure_does_not_block_db_delete(self) -> None:
        """ES 清理失败不回滚 DB 软删（尽力而为 + 记录日志）"""
        doc = _make_doc()
        db = _make_db(doc)
        es = MagicMock()
        es.delete_by_query = AsyncMock(side_effect=ConnectionError("es down"))

        result = await delete_document(doc_id="doc-1", db=db, es_client=es, milvus_collection=None)

        assert result["status"] == "ok"
        assert doc.is_deleted is True  # DB 软删仍然生效

    @pytest.mark.asyncio
    async def test_milvus_failure_does_not_block_db_delete(self) -> None:
        """Milvus 清理失败不回滚 DB 软删"""
        doc = _make_doc()
        db = _make_db(doc)
        milvus = MagicMock()
        milvus.delete = MagicMock(side_effect=RuntimeError("milvus down"))

        result = await delete_document(doc_id="doc-1", db=db, es_client=None, milvus_collection=milvus)

        assert result["status"] == "ok"
        assert doc.is_deleted is True

    @pytest.mark.asyncio
    async def test_doc_not_found_raises(self) -> None:
        """文档不存在时抛出 2001 业务错误"""
        from smartcs.shared.exceptions import SmartCSError

        db = _make_db(None)
        with pytest.raises(SmartCSError):
            await delete_document(doc_id="missing", db=db, es_client=None, milvus_collection=None)

    @pytest.mark.asyncio
    async def test_no_clients_only_soft_deletes(self) -> None:
        """ES/Milvus 均不可用时仅软删 DB，不报错"""
        doc = _make_doc()
        db = _make_db(doc)
        result = await delete_document(doc_id="doc-1", db=db, es_client=None, milvus_collection=None)
        assert result == {"status": "ok", "doc_id": "doc-1"}
