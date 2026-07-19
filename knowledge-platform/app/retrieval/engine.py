"""检索引擎 — ES 原生 RRF 混合检索

架构：ES 8.14+ 原生 RRF retriever 服务端融合 BM25+IK 与 kNN。
消除 Python 手写 RRF，单 ES 查询完成混合检索。
Reranker 精排 top-50 → top-10。

检索链路：
  query → embed_query → ES RRF(BM25 ‖ kNN) → Reranker → 合规过滤 → 缓存
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date as date_cls
from typing import Any

from elasticsearch import AsyncElasticsearch

from app.config import get_settings
from app.logging import get_logger
from app.retrieval.models import RetrievedChunk, RetrieveRequest, RetrieveResponse

logger = get_logger(__name__)

# ES keyword 过滤字段
_ES_KEYWORD_FIELDS = {
    "category", "doc_type", "card_type", "customer_tier",
    "security_level", "version", "chunk_type",
    "approval_status", "is_current_version", "doc_group",
    "model_version",
}
# ES date 过滤字段
_ES_DATE_FIELDS = {"effective_date", "expiry_date"}


def build_es_filters(filters: dict) -> list[dict]:
    """将 filters 转换为 ES bool.filter 子句列表"""
    clauses: list[dict] = []
    for key, value in filters.items():
        if value is None:
            continue
        if key in _ES_KEYWORD_FIELDS:
            clauses.append({"term": {key: value}})
        elif key in _ES_DATE_FIELDS:
            if isinstance(value, dict):
                range_clause: dict[str, Any] = {}
                if "gte" in value:
                    range_clause["gte"] = _date_to_epoch(value["gte"])
                if "lte" in value:
                    range_clause["lte"] = _date_to_epoch(value["lte"])
                if range_clause:
                    clauses.append({"range": {key: range_clause}})
            elif isinstance(value, str):
                epoch = _date_to_epoch(value)
                if epoch:
                    clauses.append({"range": {key: {"gte": epoch}}})
        elif key == "keywords":
            if isinstance(value, list):
                clauses.append({"terms": {key: value}})
            else:
                clauses.append({"term": {key: value}})
    return clauses


def _date_to_epoch(date_str: str) -> int:
    """yyyy-MM-dd → epoch 秒"""
    try:
        from datetime import datetime

        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return 0


def _build_cache_key(query: str, filters: dict, search_type: str) -> str:
    """生成检索缓存 key"""
    q_hash = hashlib.md5(query.encode()).hexdigest()[:12]  # noqa: S324
    f_str = json.dumps(filters, sort_keys=True, ensure_ascii=False) if filters else "{}"
    f_hash = hashlib.md5(f_str.encode()).hexdigest()[:8]  # noqa: S324
    return f"kp:rag:cache:{search_type}:{q_hash}:{f_hash}"


async def _search_es_rrf(
    es_client: AsyncElasticsearch,
    query: str,
    query_embedding: list[float],
    top_k: int,
    filters: dict,
    rrf_k: int = 60,
) -> list[RetrievedChunk]:
    """ES 原生 RRF 检索

    使用 RRF retriever 在服务端融合 BM25+IK 与 kNN，单查询完成混合检索。
    """
    settings = get_settings()
    index_name = settings.elasticsearch.chunks_index

    filter_clauses = build_es_filters(filters)

    # BM25 standard retriever
    standard_retriever: dict[str, Any] = {
        "standard": {
            "query": {
                "match": {"content": {"query": query, "analyzer": "ik_smart"}},
            },
        },
    }
    if filter_clauses:
        standard_retriever["standard"]["query"] = {
            "bool": {
                "must": [standard_retriever["standard"]["query"]],
                "filter": filter_clauses,
            }
        }

    # kNN retriever
    # num_candidates 建议 top_k 的 10 倍以保证召回率（ES 官方推荐）
    knn_retriever: dict[str, Any] = {
        "knn": {
            "field": "embedding",
            "query_vector": query_embedding,
            "k": top_k,
            "num_candidates": min(top_k * 10, 1000),
        },
    }
    if filter_clauses:
        knn_retriever["knn"]["filter"] = {"bool": {"filter": filter_clauses}}

    # RRF 融合
    body: dict[str, Any] = {
        "retriever": {
            "rrf": {
                "retrievers": [standard_retriever, knn_retriever],
                "rank_window_size": min(top_k * 2, 50),
                "rank_constant": rrf_k,
            },
        },
        "size": top_k,
        "_source": [
            "chunk_id", "doc_id", "content", "category", "doc_type",
            "keywords", "card_type", "customer_tier", "security_level",
            "version", "chunk_type", "parent_chunk_id", "heading_path",
            "approval_status", "is_current_version", "doc_group",
            "effective_date", "expiry_date", "model_version",
        ],
    }

    try:
        resp = await es_client.search(index=index_name, body=body)
        results: list[RetrievedChunk] = []
        for hit in resp["hits"]["hits"]:
            source = hit["_source"]
            chunk_id = source.get("chunk_id", hit["_id"])
            metadata = {
                k: v for k, v in source.items()
                if k not in ("chunk_id", "content", "doc_id")
            }
            results.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    content=source.get("content", ""),
                    score=hit["_score"] or 0.0,
                    source_doc=source.get("doc_id", ""),
                    metadata=metadata,
                )
            )
        return results
    except Exception:
        logger.exception("ES RRF 检索异常: query=%s", query)
        return []


async def _search_bm25_only(
    es_client: AsyncElasticsearch,
    query: str,
    top_k: int,
    filters: dict,
) -> list[RetrievedChunk]:
    """仅 BM25 检索（降级模式）"""
    settings = get_settings()
    index_name = settings.elasticsearch.chunks_index

    match_query: dict[str, Any] = {
        "match": {"content": {"query": query, "analyzer": "ik_smart"}}
    }
    filter_clauses = build_es_filters(filters)

    if filter_clauses:
        body = {"query": {"bool": {"must": [match_query], "filter": filter_clauses}}}
    else:
        body = {"query": match_query}

    try:
        resp = await es_client.search(index=index_name, body=body, size=top_k)
        results: list[RetrievedChunk] = []
        for hit in resp["hits"]["hits"]:
            source = hit["_source"]
            metadata = {
                k: v for k, v in source.items()
                if k not in ("chunk_id", "content", "doc_id")
            }
            results.append(
                RetrievedChunk(
                    chunk_id=source.get("chunk_id", hit["_id"]),
                    content=source.get("content", ""),
                    score=hit["_score"] or 0.0,
                    source_doc=source.get("doc_id", ""),
                    metadata=metadata,
                )
            )
        return results
    except Exception:
        logger.exception("BM25 检索异常: query=%s", query)
        return []


async def retrieve(
    request: RetrieveRequest,
    es_client: AsyncElasticsearch | None = None,
    embedding_provider: Any | None = None,
    reranker: Any | None = None,
    redis_client: Any = None,
) -> RetrieveResponse:
    """混合检索编排

    流程:
    0. Redis 缓存命中 → 直接返回
    1. embed_query → 获取查询向量
    2. ES 原生 RRF: BM25+IK ‖ kNN 服务端融合
    3. 可选 Reranker 精排
    4. 合规过滤 + 置信度阈值
    5. 截断 top_k → 写缓存
    """
    import asyncio

    start_time = time.monotonic()
    settings = get_settings()
    rrf_k = request.rrf_k if request.rrf_k is not None else settings.rag.rrf_k
    confidence_threshold = settings.rag.confidence_threshold

    # 0. 缓存检查
    cache_key = _build_cache_key(request.query, request.filters or {}, request.search_type)
    if redis_client and request.search_type != "vector_only":
        try:
            cached_raw = await redis_client.get(cache_key)
            if cached_raw:
                cached_data = json.loads(cached_raw)
                cached_results = [
                    RetrievedChunk(**c) for c in cached_data["results"]
                ]
                return RetrieveResponse(
                    results=cached_results[:request.top_k],
                    total_candidates=cached_data["total_candidates"],
                    latency_ms=int((time.monotonic() - start_time) * 1000),
                )
        except Exception:
            logger.debug("Redis 缓存读取失败")

    # 扩展候选集
    expanded_k = request.top_k * 3

    # 银行合规过滤
    compliance_filters = dict(request.filters or {})
    compliance_filters["approval_status"] = "PUBLISHED"
    compliance_filters["is_current_version"] = True
    if not request.include_expired:
        today_str = date_cls.today().isoformat()
        compliance_filters["effective_date"] = {"lte": today_str}

    # 影子索引灰度：检索时可指定 model_version 过滤
    # 切换流程：1)新模型灌入用新 version → 2)灰度检索验证 → 3)切换默认 → 4)清理旧
    settings = get_settings()
    if settings.rag.shadow_model_version:
        compliance_filters["model_version"] = settings.rag.shadow_model_version
    elif request.model_version:
        compliance_filters["model_version"] = request.model_version

    fused: list[RetrievedChunk] = []

    if request.search_type == "hybrid":
        if es_client is None or embedding_provider is None:
            logger.warning("hybrid 检索需要 ES + embedding_provider，降级到空结果")
        else:
            try:
                query_embedding = await embedding_provider.embed_query(request.query)
                fused = await _search_es_rrf(
                    es_client, request.query, query_embedding,
                    expanded_k, compliance_filters, rrf_k,
                )
            except Exception:
                logger.exception("hybrid 检索失败，尝试 BM25 only")
                if es_client:
                    fused = await _search_bm25_only(
                        es_client, request.query, expanded_k, compliance_filters,
                    )

    elif request.search_type == "bm25_only":
        if es_client:
            fused = await _search_bm25_only(
                es_client, request.query, expanded_k, compliance_filters,
            )

    elif request.search_type == "vector_only":
        if es_client and embedding_provider:
            try:
                query_embedding = await embedding_provider.embed_query(request.query)
                settings = get_settings()
                index_name = settings.elasticsearch.chunks_index
                filter_clauses = build_es_filters(compliance_filters)
                knn_body: dict[str, Any] = {
                    "field": "embedding",
                    "query_vector": query_embedding,
                    "k": expanded_k,
                    "num_candidates": min(expanded_k * 4, 200),
                }
                if filter_clauses:
                    knn_body["filter"] = {"bool": {"filter": filter_clauses}}
                resp = await es_client.search(
                    index=index_name, knn=knn_body, size=expanded_k,
                )
                for hit in resp["hits"]["hits"]:
                    source = hit["_source"]
                    metadata = {
                        k: v for k, v in source.items()
                        if k not in ("chunk_id", "content", "doc_id")
                    }
                    fused.append(
                        RetrievedChunk(
                            chunk_id=source.get("chunk_id", hit["_id"]),
                            content=source.get("content", ""),
                            score=hit["_score"] or 0.0,
                            source_doc=source.get("doc_id", ""),
                            metadata=metadata,
                        )
                    )
            except Exception:
                logger.exception("vector_only 检索失败")

    # Reranker 精排
    use_reranker = False
    if request.rerank and reranker and fused:
        candidates = fused[: request.top_k * 2]
        content_list = [c.content for c in candidates]
        try:
            rerank_results = await asyncio.to_thread(
                reranker.rerank, request.query, content_list, request.top_k,
            )
            reranked: list[RetrievedChunk] = []
            for rr in rerank_results:
                if 0 <= rr.index < len(candidates):
                    original = candidates[rr.index]
                    reranked.append(
                        RetrievedChunk(
                            chunk_id=original.chunk_id,
                            content=original.content,
                            score=rr.relevance_score,
                            source_doc=original.source_doc,
                            metadata=original.metadata,
                        )
                    )
            if reranked:
                fused = reranked
                use_reranker = True
        except Exception:
            logger.warning("Reranker 调用失败，使用 RRF 结果", exc_info=True)

    # 置信度过滤
    if confidence_threshold > 0 and fused:
        fused = [c for c in fused if c.score >= confidence_threshold]

    # 截断
    fused = fused[: request.top_k]
    latency_ms = int((time.monotonic() - start_time) * 1000)

    # 写缓存
    if redis_client and fused and request.search_type != "vector_only":
        try:
            cache_data = {
                "results": [c.model_dump() for c in fused],
                "total_candidates": len(fused),
            }
            await redis_client.setex(cache_key, 300, json.dumps(cache_data, ensure_ascii=False))
        except Exception:
            logger.debug("Redis 缓存写入失败")

    return RetrieveResponse(
        results=fused,
        total_candidates=len(fused),
        latency_ms=latency_ms,
    )
