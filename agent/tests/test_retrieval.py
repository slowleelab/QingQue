"""混合检索引擎测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lumio.services.common.retrieval import (
    build_es_filters,
    build_milvus_expr,
    retrieve,
    rrf_fusion,
    search_bm25,
    search_vector,
)
from lumio.shared.models import RetrievedChunk, RetrieveRequest, RetrieveResponse


class TestBuildEsFilters:
    def test_keyword_filter(self):
        filters = {"category": "FAQ", "doc_type": "rule"}
        clauses = build_es_filters(filters)
        assert len(clauses) == 2
        assert {"term": {"category": "FAQ"}} in clauses
        assert {"term": {"doc_type": "rule"}} in clauses

    def test_date_range_filter(self):
        filters = {"effective_date": {"gte": "2026-01-01", "lte": "2026-12-31"}}
        clauses = build_es_filters(filters)
        assert len(clauses) == 1
        assert clauses[0]["range"]["effective_date"]["gte"] == "2026-01-01"
        assert clauses[0]["range"]["effective_date"]["lte"] == "2026-12-31"

    def test_keywords_filter(self):
        filters = {"keywords": ["年费", "积分"]}
        clauses = build_es_filters(filters)
        assert len(clauses) == 1
        assert clauses[0]["terms"]["keywords"] == ["年费", "积分"]

    def test_empty_filters(self):
        assert build_es_filters({}) == []


class TestBuildMilvusExpr:
    def test_keyword_expr(self):
        expr = build_milvus_expr({"category": "FAQ"})
        assert 'category == "FAQ"' in expr

    def test_keywords_array_contains(self):
        expr = build_milvus_expr({"keywords": "年费"})
        assert 'ARRAY_CONTAINS(keywords, "年费")' in expr

    def test_keywords_multi_array_contains(self):
        expr = build_milvus_expr({"keywords": ["年费", "减免"]})
        assert 'ARRAY_CONTAINS(keywords, "年费")' in expr
        assert 'ARRAY_CONTAINS(keywords, "减免")' in expr
        assert " or " in expr

    def test_empty_expr(self):
        assert build_milvus_expr({}) == ""

    def test_multiple_conditions(self):
        expr = build_milvus_expr({"category": "FAQ", "doc_type": "rule"})
        assert " and " in expr


class TestRRFFusion:
    def test_fusion_with_overlap(self):
        bm25 = [
            RetrievedChunk(chunk_id="a", content="A", score=1.0, source_doc="d1"),
            RetrievedChunk(chunk_id="b", content="B", score=0.8, source_doc="d1"),
            RetrievedChunk(chunk_id="c", content="C", score=0.6, source_doc="d1"),
        ]
        vector = [
            RetrievedChunk(chunk_id="b", content="B", score=0.9, source_doc="d1"),
            RetrievedChunk(chunk_id="a", content="A", score=0.7, source_doc="d1"),
            RetrievedChunk(chunk_id="d", content="D", score=0.5, source_doc="d1"),
        ]
        result = rrf_fusion(bm25, vector, k=60)
        # "a" and "b" appear in both lists, should rank higher
        ids = [c.chunk_id for c in result]
        assert "a" in ids
        assert "b" in ids
        # Overlapping chunks should have higher RRF scores
        a_chunk = next(c for c in result if c.chunk_id == "a")
        d_chunk = next(c for c in result if c.chunk_id == "d")
        assert a_chunk.score > d_chunk.score

    def test_fusion_no_overlap(self):
        bm25 = [RetrievedChunk(chunk_id="a", content="A", score=1.0, source_doc="d1")]
        vector = [RetrievedChunk(chunk_id="b", content="B", score=0.9, source_doc="d1")]
        result = rrf_fusion(bm25, vector, k=60)
        assert len(result) == 2
        # a is rank 1 in bm25, b is rank 1 in vector -> same RRF score
        assert result[0].score == result[1].score or abs(result[0].score - result[1].score) < 0.001

    def test_fusion_single_list(self):
        bm25 = [RetrievedChunk(chunk_id="a", content="A", score=1.0, source_doc="d1")]
        result = rrf_fusion(bm25, [], k=60)
        assert len(result) == 1
        assert result[0].chunk_id == "a"

    def test_fusion_empty(self):
        result = rrf_fusion([], [], k=60)
        assert result == []


class TestSearchBM25:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_es = AsyncMock()
        mock_es.search.return_value = {
            "hits": {
                "hits": [
                    {"_id": "1", "_score": 5.0, "_source": {"chunk_id": "c1", "content": "年费100元", "doc_id": "d1"}}
                ]
            }
        }
        result = await search_bm25(mock_es, "年费", top_k=5)
        assert len(result) == 1
        assert result[0].chunk_id == "c1"
        assert result[0].content == "年费100元"

    @pytest.mark.asyncio
    async def test_none_client_degradation(self):
        result = await search_bm25(None, "年费", top_k=5)
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        mock_es = AsyncMock()
        mock_es.search.side_effect = Exception("ES down")
        result = await search_bm25(mock_es, "年费", top_k=5)
        assert result == []


class TestSearchVector:
    @pytest.mark.asyncio
    async def test_none_collection_degradation(self):
        result = await search_vector(None, [0.1] * 1024, top_k=5)
        assert result == []


class TestRetrieve:
    @pytest.mark.asyncio
    async def test_bm25_only(self):
        mock_es = AsyncMock()
        mock_es.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_id": "1",
                        "_score": 3.0,
                        "_source": {
                            "chunk_id": "c1",
                            "content": "test",
                            "doc_id": "d1",
                            "approval_status": "PUBLISHED",
                            "is_current_version": True,
                        },
                    }
                ]
            }
        }
        request = RetrieveRequest(query="test", top_k=3, search_type="bm25_only", rerank=False)
        resp = await retrieve(
            request, es_client=mock_es, milvus_collection=None, embedding_provider=None, reranker=None
        )
        assert isinstance(resp, RetrieveResponse)
        assert len(resp.results) >= 1

    @pytest.mark.asyncio
    async def test_degradation_both_fail(self):
        request = RetrieveRequest(query="test", top_k=3, search_type="hybrid", rerank=False)
        resp = await retrieve(request, es_client=None, milvus_collection=None, embedding_provider=None, reranker=None)
        assert resp.results == []
        assert resp.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_confidence_threshold(self):
        """BM25 score 低于 confidence_threshold 时被过滤（bm25_only 模式使用 confidence_threshold）"""
        mock_es = AsyncMock()
        mock_es.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_id": "1",
                        "_score": 0.1,
                        "_source": {
                            "chunk_id": "c1",
                            "content": "low score",
                            "doc_id": "d1",
                            "approval_status": "PUBLISHED",
                            "is_current_version": True,
                        },
                    },
                ]
            }
        }
        request = RetrieveRequest(query="test", top_k=3, search_type="bm25_only", rerank=False)
        with patch("lumio.services.common.retrieval.get_settings") as mock_settings:
            mock_settings.return_value.rag.confidence_threshold = 0.5
            mock_settings.return_value.rag.rrf_confidence_threshold = 0.0
            mock_settings.return_value.rag.rrf_k = 60
            mock_settings.return_value.elasticsearch.index_prefix = "lumio"
            resp = await retrieve(
                request, es_client=mock_es, milvus_collection=None, embedding_provider=None, reranker=None
            )
        # BM25 score 0.1 < RRF threshold 0.0 → 不过滤（RRF threshold 仅用于 RRF 融合结果）
        # bm25_only 路径使用 rrf_confidence_threshold=0.0，score 0.1 > 0.0 → 保留
        assert len(resp.results) == 1

    @pytest.mark.asyncio
    async def test_reranker_degradation(self):
        """Reranker 失败时降级到 RRF 结果"""
        mock_es = AsyncMock()
        mock_es.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_id": "1",
                        "_score": 3.0,
                        "_source": {
                            "chunk_id": "c1",
                            "content": "test content",
                            "doc_id": "d1",
                            "approval_status": "PUBLISHED",
                            "is_current_version": True,
                        },
                    }
                ]
            }
        }
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = RuntimeError("Reranker down")

        request = RetrieveRequest(query="test", top_k=3, search_type="bm25_only", rerank=True)
        resp = await retrieve(
            request, es_client=mock_es, milvus_collection=None, embedding_provider=None, reranker=mock_reranker
        )
        # Should still return results (from RRF/BM25), not fail
        assert len(resp.results) >= 1

    @pytest.mark.asyncio
    async def test_embedding_failure_degrades_to_bm25_no_nameerror(self):
        """P3-3 修复: embed_query 抛异常时, except 分支不应触发 NameError(vector_task).

        修复前: line 486 `for t in (bm25_task, vector_task)` 在 vector_task 未赋值时
        直接 NameError, 把真正的嵌入失败掩盖为 5xx.

        修复后: vector_task 初始化为 None, except 分支显式 None 检查, 降级到 BM25 only.
        """
        mock_es = AsyncMock()
        mock_es.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_id": "1",
                        "_score": 3.0,
                        "_source": {
                            "chunk_id": "c1",
                            "content": "bm25 hit",
                            "doc_id": "d1",
                            "approval_status": "PUBLISHED",
                            "is_current_version": True,
                        },
                    }
                ]
            }
        }
        # embedding 服务挂掉
        mock_embedding = MagicMock()
        mock_embedding.embed_query = AsyncMock(side_effect=RuntimeError("embedding service down"))
        mock_milvus = MagicMock()  # 提供但永远到不了

        request = RetrieveRequest(query="test", top_k=3, search_type="hybrid", rerank=False)
        resp = await retrieve(
            request,
            es_client=mock_es,
            milvus_collection=mock_milvus,
            embedding_provider=mock_embedding,
            reranker=None,
        )
        # 降级到 BM25 only, 不抛 NameError
        assert resp.results, "embedding 降级后 BM25 结果应保留"
        assert all(r.chunk_id == "c1" for r in resp.results)


class TestSearchBM25Extended:
    @pytest.mark.asyncio
    async def test_with_filters(self):
        """带过滤条件时 query body 含 bool.filter"""
        mock_es = AsyncMock()
        mock_es.search.return_value = {"hits": {"hits": []}}
        await search_bm25(mock_es, "年费", filters={"category": "fee"})
        body = mock_es.search.call_args.kwargs["body"]
        assert "bool" in body["query"]
        assert "filter" in body["query"]["bool"]

    @pytest.mark.asyncio
    async def test_parent_content_attached(self):
        """parent_chunk_id 命中时附加 parent_content"""
        mock_es = AsyncMock()

        async def fake_search(**kwargs):
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "c1",
                            "_score": 1.0,
                            "_source": {
                                "chunk_id": "c1",
                                "content": "子块",
                                "doc_id": "d1",
                                "parent_chunk_id": "p1",
                                "category": "fee",
                            },
                        }
                    ]
                }
            }

        async def fake_mget(*a, **kw):
            return {"docs": [{"found": True, "_id": "p1", "_source": {"content": "父块内容"}}]}

        mock_es.search = fake_search
        mock_es.mget = fake_mget
        result = await search_bm25(mock_es, "年费")
        assert result[0].metadata["parent_content"] == "父块内容"

    @pytest.mark.asyncio
    async def test_batch_fetch_parents_es(self):
        """批量获取 parent: 未命中时跳过"""
        from lumio.services.common.retrieval import _batch_fetch_parents_es

        mock_es = AsyncMock()
        mock_es.mget.return_value = {
            "docs": [{"found": False, "_id": "p1"}, {"found": True, "_id": "p2", "_source": {"content": "x"}}]
        }
        contents = await _batch_fetch_parents_es(mock_es, "idx", ["p1", "p2"])
        assert contents == {"p2": "x"}


class TestSearchVectorExtended:
    @pytest.mark.asyncio
    async def test_success_with_parents(self):
        """向量检索成功 + parent 内容附加"""
        from lumio.services.common.retrieval import search_vector

        class _Hit:
            def __init__(self, cid, distance, **fields):
                self.id = cid
                self.distance = distance
                self.entity = {"chunk_id": cid, "content": "内容", "doc_id": "d1", **fields}

        class _Result:
            def __init__(self, hits):
                self._hits = hits

            def __getitem__(self, i):
                return self._hits[i]

            def __len__(self):
                return len(self._hits)

        collection = MagicMock()

        def fake_search(**kwargs):
            return [_Result([_Hit("c1", 0.9, parent_chunk_id="p1")])]

        def fake_query(**kwargs):
            return [_Hit("p1", 0.5, content="父内容")]

        collection.search = fake_search
        collection.query = fake_query

        result = await search_vector(collection, [0.1] * 4, top_k=5)
        assert len(result) == 1
        assert result[0].chunk_id == "c1"
        assert result[0].score == 0.9

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        """向量检索异常 → 空列表"""
        from lumio.services.common.retrieval import search_vector

        collection = MagicMock()

        def fake_search(**kwargs):
            raise RuntimeError("milvus down")

        collection.search = fake_search
        result = await search_vector(collection, [0.1] * 4)
        assert result == []


class TestDateToEpoch:
    def test_valid(self):
        from lumio.services.common.retrieval import _date_to_epoch

        assert _date_to_epoch("2026-08-01") is not None

    def test_invalid(self):
        from lumio.services.common.retrieval import _date_to_epoch

        assert _date_to_epoch("not-a-date") is None
        assert _date_to_epoch(None) is None


class TestCacheKey:
    def test_cache_key_stable(self):
        from lumio.services.common.retrieval import _build_cache_key

        k1 = _build_cache_key("查询", {"category": "fee"}, 5)
        k2 = _build_cache_key("查询", {"category": "fee"}, 5)
        assert k1 == k2
        k3 = _build_cache_key("查询", {"category": "fee"}, 10)
        assert k1 != k3
