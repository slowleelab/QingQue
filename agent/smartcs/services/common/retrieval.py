"""混合检索引擎

实现 BM25 + 向量 + RRF 融合检索，支持 Reranker 精排和 Parent-Child 分块展开。
降级策略：Milvus 不可用 → BM25 only；ES 不可用 → 向量 only；均不可用 → 空结果。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from smartcs.shared.config import get_settings
from smartcs.shared.models import RetrievedChunk, RetrieveRequest, RetrieveResponse

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch
    from pymilvus import Collection

    from smartcs.services.common.embedding import EmbeddingProvider
    from smartcs.services.common.reranker import RerankerProvider

logger = logging.getLogger(__name__)

# ES keyword 过滤字段
_ES_KEYWORD_FIELDS = {"category", "doc_type", "card_type", "customer_tier", "security_level", "version", "chunk_type"}
# ES date 过滤字段
_ES_DATE_FIELDS = {"effective_date", "expiry_date"}


def build_es_filters(filters: dict) -> list[dict]:
    """将 RetrieveRequest.filters 转换为 ES bool.filter 子句列表

    - keyword 字段 → term 查询
    - date 字段 → range 查询 (gte/lte, yyyy-MM-dd)
    - keywords → terms 查询（匹配任一关键词）
    """
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
                    range_clause["gte"] = value["gte"]
                if "lte" in value:
                    range_clause["lte"] = value["lte"]
                if range_clause:
                    clauses.append({"range": {key: range_clause}})
            elif isinstance(value, str):
                # 简写: 单个日期值视为 gte
                clauses.append({"range": {key: {"gte": value}}})
        elif key == "keywords":
            if isinstance(value, list):
                clauses.append({"terms": {key: value}})
            else:
                clauses.append({"term": {key: value}})
    return clauses


def build_milvus_expr(filters: dict) -> str:
    """将 RetrieveRequest.filters 转换为 Milvus 过滤表达式字符串

    - keyword 字段 → field == "value"
    - date 字段 → field >= epoch_ms (整型比较)
    - keywords → keywords like "%value%" (VARCHAR 字段)
    - 多条件用 " and " 连接
    - 空 filters 返回 ""
    """
    conditions: list[str] = []
    for key, value in filters.items():
        if value is None:
            continue
        if key in _ES_KEYWORD_FIELDS or key == "chunk_type":
            conditions.append(f'{key} == "{value}"')
        elif key in _ES_DATE_FIELDS:
            # 将日期字符串转为 epoch 毫秒
            if isinstance(value, dict):
                if "gte" in value:
                    epoch_ms = _date_to_epoch_ms(value["gte"])
                    if epoch_ms:
                        conditions.append(f"{key} >= {epoch_ms}")
                if "lte" in value:
                    epoch_ms = _date_to_epoch_ms(value["lte"])
                    if epoch_ms:
                        conditions.append(f"{key} <= {epoch_ms}")
            elif isinstance(value, str):
                epoch_ms = _date_to_epoch_ms(value)
                if epoch_ms:
                    conditions.append(f"{key} >= {epoch_ms}")
        elif key == "keywords":
            if isinstance(value, list):
                for kw in value:
                    conditions.append(f'keywords like "%{kw}%"')
            else:
                conditions.append(f'keywords like "%{value}%"')
    return " and ".join(conditions)


def _date_to_epoch_ms(date_str: str) -> int | None:
    """将 yyyy-MM-dd 日期字符串转为 epoch 毫秒"""
    try:
        from datetime import datetime

        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


async def search_bm25(
    es_client: AsyncElasticsearch | None,
    query: str,
    top_k: int = 5,
    filters: dict | None = None,
) -> list[RetrievedChunk]:
    """BM25 全文检索（Elasticsearch + IK 分词）

    Args:
        es_client: ES 异步客户端（None 时返回空列表，触发降级）
        query: 查询文本
        top_k: 返回结果数
        filters: 过滤条件

    Returns:
        RetrievedChunk 列表；异常时返回空列表
    """
    if es_client is None:
        return []

    settings = get_settings()
    index_name = f"{settings.elasticsearch.index_prefix}_knowledge"

    # 构建 ES 查询体
    match_query = {"match": {"content": {"query": query, "analyzer": "ik_smart"}}}
    filter_clauses = build_es_filters(filters or {})

    if filter_clauses:
        body: dict[str, Any] = {"query": {"bool": {"must": [match_query], "filter": filter_clauses}}}
    else:
        body = {"query": match_query}

    try:
        resp = await es_client.search(index=index_name, body=body, size=top_k)
        results: list[RetrievedChunk] = []
        for hit in resp["hits"]["hits"]:
            source = hit["_source"]
            chunk_id = source.get("chunk_id", hit["_id"])
            # Parent-Child 展开：如果是 child chunk，查询 parent 内容
            parent_content = None
            parent_chunk_id = source.get("parent_chunk_id")
            if parent_chunk_id:
                parent_content = await _fetch_parent_content(es_client, index_name, parent_chunk_id)

            metadata = {
                k: v for k, v in source.items() if k not in ("chunk_id", "content", "doc_id")
            }
            if parent_content:
                metadata["parent_content"] = parent_content

            results.append(
                RetrievedChunk(
                    chunk_id=chunk_id,
                    content=source.get("content", ""),
                    score=hit["_score"],
                    source_doc=source.get("doc_id", ""),
                    metadata=metadata,
                )
            )
        return results
    except Exception:
        logger.exception("BM25 检索异常: query=%s", query)
        return []


async def _fetch_parent_content(
    es_client: AsyncElasticsearch,
    index_name: str,
    parent_chunk_id: str,
) -> str | None:
    """从 ES 获取 parent chunk 的内容"""
    try:
        resp = await es_client.get(index=index_name, id=parent_chunk_id)
        return resp["_source"].get("content")
    except Exception:
        logger.debug("获取 parent chunk 内容失败: parent_chunk_id=%s", parent_chunk_id)
        return None


async def search_vector(
    milvus_collection: Collection | None,
    query_embedding: list[float],
    top_k: int = 5,
    filters: dict | None = None,
) -> list[RetrievedChunk]:
    """向量检索（Milvus IVF_FLAT COSINE）

    只搜索 child chunks（有 embedding 的块），不搜索 parent chunks。

    Args:
        milvus_collection: Milvus Collection 对象（None 时返回空列表，触发降级）
        query_embedding: 查询向量
        top_k: 返回结果数
        filters: 过滤条件

    Returns:
        RetrievedChunk 列表；异常时返回空列表
    """
    if milvus_collection is None:
        return []

    # 构建 Milvus 过滤表达式
    base_expr = build_milvus_expr(filters or {})
    # Milvus 中只存了有 embedding 的 chunk，所以不需要额外过滤

    search_params = {"metric_type": "COSINE", "params": {"nprobe": 16}}
    output_fields = [
        "chunk_id", "doc_id", "content", "category", "doc_type",
        "keywords", "card_type", "customer_tier", "chunk_type", "parent_chunk_id",
    ]

    try:
        results_raw = await asyncio.to_thread(
            milvus_collection.search,
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr=base_expr if base_expr else None,
            output_fields=output_fields,
        )

        results: list[RetrievedChunk] = []
        if results_raw and len(results_raw) > 0:
            for hit in results_raw[0]:
                entity = hit.entity
                chunk_id = entity.get("chunk_id") or str(hit.id)
                parent_chunk_id = entity.get("parent_chunk_id")

                # Parent-Child 展开
                parent_content = None
                if parent_chunk_id:
                    parent_content = await _fetch_parent_from_milvus(milvus_collection, parent_chunk_id)

                metadata: dict[str, Any] = {}
                for field_name in output_fields:
                    if field_name not in ("chunk_id", "content", "doc_id") and entity.get(field_name) is not None:
                        metadata[field_name] = entity.get(field_name)
                if parent_content:
                    metadata["parent_content"] = parent_content

                results.append(
                    RetrievedChunk(
                        chunk_id=chunk_id,
                        content=entity.get("content", ""),
                        score=hit.distance,  # Milvus COSINE 返回 0~1 相似度
                        source_doc=entity.get("doc_id", ""),
                        metadata=metadata,
                    )
                )
        return results
    except Exception:
        logger.exception("向量检索异常: top_k=%d", top_k)
        return []


async def _fetch_parent_from_milvus(
    milvus_collection: Collection,
    parent_chunk_id: str,
) -> str | None:
    """从 Milvus 获取 parent chunk 的内容"""
    try:
        results = await asyncio.to_thread(
            milvus_collection.query,
            expr=f'chunk_id == "{parent_chunk_id}"',
            output_fields=["content"],
        )
        if results and len(results) > 0:
            return results[0].get("content")
    except Exception:
        logger.debug("从 Milvus 获取 parent 内容失败: parent_chunk_id=%s", parent_chunk_id)
    return None


def rrf_fusion(
    bm25_results: list[RetrievedChunk],
    vector_results: list[RetrievedChunk],
    k: int = 60,
) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion 融合 BM25 和向量检索结果

    RRF 公式: score(d) = Σ 1/(k + rank_i)
    - 以 chunk_id 去重
    - 同时出现在两个列表的 chunk 分数求和
    - 按 RRF 分数降序排列

    Args:
        bm25_results: BM25 检索结果
        vector_results: 向量检索结果
        k: RRF 常数（默认 60）

    Returns:
        融合后的 RetrievedChunk 列表
    """
    # chunk_id → (cumulative_score, RetrievedChunk)
    score_map: dict[str, tuple[float, RetrievedChunk]] = {}

    for rank, chunk in enumerate(bm25_results, start=1):
        rrf_score = 1.0 / (k + rank)
        if chunk.chunk_id in score_map:
            existing_score, existing_chunk = score_map[chunk.chunk_id]
            score_map[chunk.chunk_id] = (existing_score + rrf_score, existing_chunk)
        else:
            score_map[chunk.chunk_id] = (rrf_score, chunk)

    for rank, chunk in enumerate(vector_results, start=1):
        rrf_score = 1.0 / (k + rank)
        if chunk.chunk_id in score_map:
            existing_score, existing_chunk = score_map[chunk.chunk_id]
            score_map[chunk.chunk_id] = (existing_score + rrf_score, existing_chunk)
        else:
            score_map[chunk.chunk_id] = (rrf_score, chunk)

    # 按 RRF 分数降序排列
    sorted_results = sorted(score_map.values(), key=lambda x: x[0], reverse=True)

    # 更新 score 为 RRF 分数
    return [
        RetrievedChunk(
            chunk_id=chunk.chunk_id,
            content=chunk.content,
            score=score,
            source_doc=chunk.source_doc,
            metadata=chunk.metadata,
        )
        for score, chunk in sorted_results
    ]


async def retrieve(
    request: RetrieveRequest,
    es_client: AsyncElasticsearch | None = None,
    milvus_collection: Collection | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    reranker: RerankerProvider | None = None,
) -> RetrieveResponse:
    """混合检索编排

    流程:
    1. 按 search_type 分发: hybrid / bm25_only / vector_only
    2. Hybrid: 并发执行 BM25 + 向量检索，RRF 融合
    3. 可选 Reranker 精排
    4. 置信度阈值过滤
    5. 截断到 top_k

    降级矩阵:
    | ES | Milvus | 行为 |
    |----|--------|------|
    | ✓  | ✓      | Hybrid + RRF |
    | ✓  | ✗      | BM25 only |
    | ✗  | ✓      | Vector only |
    | ✗  | ✗      | 空结果 |
    """
    start_time = time.monotonic()
    settings = get_settings()
    rrf_k = request.rrf_k if request.rrf_k is not None else settings.rag.rrf_k
    confidence_threshold = settings.rag.confidence_threshold

    # 扩展候选集
    expanded_k = request.top_k * 3

    bm25_results: list[RetrievedChunk] = []
    vector_results: list[RetrievedChunk] = []

    if request.search_type == "hybrid":
        # 并发执行 BM25 + 向量检索
        bm25_task = search_bm25(es_client, request.query, expanded_k, request.filters)

        # 向量检索需要先 embed query
        if embedding_provider and milvus_collection:
            try:
                query_embedding = await embedding_provider.embed_query(request.query)
                vector_task = search_vector(milvus_collection, query_embedding, expanded_k, request.filters)
                bm25_results, vector_results = await asyncio.gather(bm25_task, vector_task)
            except Exception:
                logger.warning("向量检索嵌入失败，降级到 BM25 only")
                bm25_results = await bm25_task
        else:
            bm25_results = await bm25_task

        # 融合
        if bm25_results and vector_results:
            fused = rrf_fusion(bm25_results, vector_results, k=rrf_k)
        elif bm25_results:
            fused = bm25_results
            logger.info("向量检索无结果，降级到 BM25 only")
        elif vector_results:
            fused = vector_results
            logger.info("BM25 检索无结果，降级到向量 only")
        else:
            fused = []

    elif request.search_type == "bm25_only":
        bm25_results = await search_bm25(es_client, request.query, expanded_k, request.filters)
        fused = bm25_results

    elif request.search_type == "vector_only":
        if embedding_provider and milvus_collection:
            try:
                query_embedding = await embedding_provider.embed_query(request.query)
                vector_results = await search_vector(milvus_collection, query_embedding, expanded_k, request.filters)
            except Exception:
                logger.warning("向量检索失败: query=%s", request.query)
        fused = vector_results

    else:
        logger.warning("未知 search_type: %s", request.search_type)
        fused = []

    # Reranker 精排
    if request.rerank and reranker and fused:
        candidate_count = request.top_k * 2
        candidates = fused[:candidate_count]
        content_list = [c.content for c in candidates]

        try:
            rerank_results = await asyncio.to_thread(
                reranker.rerank, request.query, content_list, request.top_k
            )
            # 映射回 RetrievedChunk
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
        except Exception:
            logger.warning("Reranker 调用失败，使用 RRF 结果", exc_info=True)

    # 置信度阈值过滤
    if confidence_threshold > 0:
        fused = [c for c in fused if c.score >= confidence_threshold]

    # 截断到 top_k
    fused = fused[: request.top_k]

    latency_ms = int((time.monotonic() - start_time) * 1000)
    total_candidates = len(bm25_results) + len(vector_results)

    return RetrieveResponse(
        results=fused,
        total_candidates=total_candidates,
        latency_ms=latency_ms,
    )
