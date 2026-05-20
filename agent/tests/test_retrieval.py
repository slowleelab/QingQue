"""混合检索引擎测试"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smartcs.services.common.retrieval import (
    build_es_filters,
    build_milvus_expr,
    retrieve,
    rrf_fusion,
    search_bm25,
    search_vector,
)
from smartcs.shared.models import RetrievedChunk, RetrieveRequest, RetrieveResponse


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

    def test_keywords_like_expr(self):
        expr = build_milvus_expr({"keywords": "年费"})
        assert 'keywords like "%年费%"' in expr

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
            "hits": {"hits": [{"_id": "1", "_score": 3.0, "_source": {"chunk_id": "c1", "content": "test", "doc_id": "d1"}}]}
        }
        request = RetrieveRequest(query="test", top_k=3, search_type="bm25_only", rerank=False)
        resp = await retrieve(request, es_client=mock_es, milvus_collection=None, embedding_provider=None, reranker=None)
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
        """低于置信度阈值的结果被过滤"""
        mock_es = AsyncMock()
        mock_es.search.return_value = {
            "hits": {
                "hits": [
                    {"_id": "1", "_score": 0.1, "_source": {"chunk_id": "c1", "content": "low score", "doc_id": "d1"}},
                ]
            }
        }
        request = RetrieveRequest(query="test", top_k=3, search_type="bm25_only", rerank=False)
        with patch("smartcs.services.common.retrieval.get_settings") as mock_settings:
            mock_settings.return_value.rag.confidence_threshold = 0.5
            mock_settings.return_value.rag.rrf_k = 60
            mock_settings.return_value.elasticsearch.index_prefix = "smartcs"
            resp = await retrieve(request, es_client=mock_es, milvus_collection=None, embedding_provider=None, reranker=None)
        # Score 0.1 < threshold 0.5, should be filtered
        assert len(resp.results) == 0

    @pytest.mark.asyncio
    async def test_reranker_degradation(self):
        """Reranker 失败时降级到 RRF 结果"""
        mock_es = AsyncMock()
        mock_es.search.return_value = {
            "hits": {"hits": [{"_id": "1", "_score": 3.0, "_source": {"chunk_id": "c1", "content": "test content", "doc_id": "d1"}}]}
        }
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = RuntimeError("Reranker down")

        request = RetrieveRequest(query="test", top_k=3, search_type="bm25_only", rerank=True)
        resp = await retrieve(request, es_client=mock_es, milvus_collection=None, embedding_provider=None, reranker=mock_reranker)
        # Should still return results (from RRF/BM25), not fail
        assert len(resp.results) >= 1
