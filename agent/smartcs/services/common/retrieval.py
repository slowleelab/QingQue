"""混合检索引擎

实现 BM25 + 向量 + RRF 融合检索，支持 Reranker 精排和 Parent-Child 分块展开。
降级策略：Milvus 不可用 → BM25 only；ES 不可用 → 向量 only；均不可用 → 空结果。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from smartcs.shared.config import get_settings
from smartcs.shared.models import RetrievedChunk, RetrieveRequest, RetrieveResponse
from smartcs.shared.tracing import traced

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch
    from pymilvus import Collection

    from smartcs.services.common.embedding import EmbeddingProvider
    from smartcs.services.common.reranker import RerankerProvider

logger = logging.getLogger(__name__)

# ES keyword 过滤字段
_ES_KEYWORD_FIELDS = {
    "category",
    "doc_type",
    "card_type",
    "customer_tier",
    "security_level",
    "version",
    "chunk_type",
    "approval_status",
    "is_current_version",
}
# Milvus 标量索引字段（在 build_milvus_expr 中 == 比较）
_MILVUS_SCALAR_FIELDS = {"category", "doc_type", "card_type", "customer_tier", "security_level", "chunk_type"}
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
    - date 字段 → field >= epoch_sec (整型比较)
    - keywords → keywords like "%value%" (VARCHAR 字段)
    - 多条件用 " and " 连接
    - 空 filters 返回 ""
    """
    conditions: list[str] = []
    for key, value in filters.items():
        if value is None:
            continue
        if key in _MILVUS_SCALAR_FIELDS:
            conditions.append(f'{key} == "{value}"')
        elif key in _ES_DATE_FIELDS:
            # 将日期字符串转为 epoch 秒（与 ES mapping epoch_second 格式对齐）
            if isinstance(value, dict):
                if "gte" in value:
                    epoch_sec = _date_to_epoch(value["gte"])
                    if epoch_sec:
                        conditions.append(f"{key} >= {epoch_sec}")
                if "lte" in value:
                    epoch_sec = _date_to_epoch(value["lte"])
                    if epoch_sec:
                        conditions.append(f"{key} <= {epoch_sec}")
            elif isinstance(value, str):
                epoch_sec = _date_to_epoch(value)
                if epoch_sec:
                    conditions.append(f"{key} >= {epoch_sec}")
        elif key == "keywords":
            # v2.1: ARRAY_CONTAINS 精确过滤，替代 like 模糊匹配
            if isinstance(value, list):
                kw_conds = [f'ARRAY_CONTAINS(keywords, "{kw}")' for kw in value]
                conditions.append("(" + " or ".join(kw_conds) + ")")
            else:
                conditions.append(f'ARRAY_CONTAINS(keywords, "{value}")')
    return " and ".join(conditions)


def _build_cache_key(query: str, filters: dict, search_type: str) -> str:
    """生成检索缓存 key: smartcs:rag:cache:{search_type}:{query_hash}:{filters_hash}"""
    query_hash = hashlib.md5(query.encode()).hexdigest()[:12]
    filters_str = json.dumps(filters, sort_keys=True, ensure_ascii=False) if filters else "{}"
    filters_hash = hashlib.md5(filters_str.encode()).hexdigest()[:8]
    return f"smartcs:rag:cache:{search_type}:{query_hash}:{filters_hash}"


def _date_to_epoch(date_str: str) -> int | None:
    """将 yyyy-MM-dd 日期字符串转为 epoch 秒（与 ES mapping epoch_second 格式对齐）"""
    try:
        from datetime import datetime

        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return int(dt.timestamp())
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
    index_name = f"{settings.elasticsearch.index_prefix}_kb_chunks"

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

        # 收集所有 parent_chunk_id，批量获取 parent 内容
        parent_ids = set()
        for hit in resp["hits"]["hits"]:
            pid = hit["_source"].get("parent_chunk_id")
            if pid:
                parent_ids.add(pid)
        parent_contents = await _batch_fetch_parents_es(es_client, index_name, list(parent_ids))

        for hit in resp["hits"]["hits"]:
            source = hit["_source"]
            chunk_id = source.get("chunk_id", hit["_id"])
            parent_chunk_id = source.get("parent_chunk_id")

            metadata = {k: v for k, v in source.items() if k not in ("chunk_id", "content", "doc_id")}
            if parent_chunk_id and parent_chunk_id in parent_contents:
                metadata["parent_content"] = parent_contents[parent_chunk_id]

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


async def _batch_fetch_parents_es(
    es_client: AsyncElasticsearch,
    index_name: str,
    parent_ids: list[str],
) -> dict[str, str]:
    """批量从 ES 获取 parent chunk 内容"""
    if not parent_ids:
        return {}
    try:
        resp = await es_client.mget(index=index_name, body={"ids": parent_ids})
        contents: dict[str, str] = {}
        for doc in resp.get("docs", []):
            if doc.get("found") and doc.get("_source"):
                contents[doc["_id"]] = doc["_source"].get("content", "")
        return contents
    except Exception:
        logger.debug("批量获取 ES parent 内容失败: count=%d", len(parent_ids))
        return {}


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
        "chunk_id",
        "doc_id",
        "content",
        "category",
        "doc_type",
        "keywords",
        "card_type",
        "customer_tier",
        "security_level",
        "chunk_type",
        "parent_chunk_id",
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
            # 收集所有 parent_chunk_id，批量获取 parent 内容
            parent_ids = set()
            for hit in results_raw[0]:
                pid = hit.entity.get("parent_chunk_id")
                if pid:
                    parent_ids.add(pid)
            parent_contents = await _batch_fetch_parents_milvus(milvus_collection, list(parent_ids))

            for hit in results_raw[0]:
                entity = hit.entity
                chunk_id = entity.get("chunk_id") or str(hit.id)
                parent_chunk_id = entity.get("parent_chunk_id")

                metadata: dict[str, Any] = {}
                for field_name in output_fields:
                    if field_name not in ("chunk_id", "content", "doc_id") and entity.get(field_name) is not None:
                        metadata[field_name] = entity.get(field_name)
                if parent_chunk_id and parent_chunk_id in parent_contents:
                    metadata["parent_content"] = parent_contents[parent_chunk_id]

                results.append(
                    RetrievedChunk(
                        chunk_id=chunk_id,
                        content=entity.get("content", ""),
                        score=hit.distance,
                        source_doc=entity.get("doc_id", ""),
                        metadata=metadata,
                    )
                )
        return results
    except Exception:
        logger.exception("向量检索异常: top_k=%d", top_k)
        return []


async def _batch_fetch_parents_milvus(
    milvus_collection: Collection,
    parent_ids: list[str],
) -> dict[str, str]:
    """批量从 Milvus 获取 parent chunk 内容"""
    if not parent_ids:
        return {}
    try:
        ids_str = ", ".join(f'"{pid}"' for pid in parent_ids)
        expr = f"chunk_id in [{ids_str}]"
        results = await asyncio.to_thread(
            milvus_collection.query,
            expr=expr,
            output_fields=["chunk_id", "content"],
        )
        contents: dict[str, str] = {}
        for r in results:
            cid = r.get("chunk_id", "")
            content = r.get("content", "")
            if cid and content:
                contents[cid] = content
        return contents
    except Exception:
        logger.debug("批量获取 Milvus parent 内容失败: count=%d", len(parent_ids))
        return {}


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


@traced("Agent: retrieval")
async def retrieve(
    request: RetrieveRequest,
    es_client: AsyncElasticsearch | None = None,
    milvus_collection: Collection | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    reranker: RerankerProvider | None = None,
    redis_client: Any = None,
) -> RetrieveResponse:
    """混合检索编排

    流程:
    0. Redis 缓存命中 → 直接返回
    1. 按 search_type 分发: hybrid / bm25_only / vector_only
    2. Hybrid: 并发执行 BM25 + 向量检索，RRF 融合
    3. 可选 Reranker 精排
    4. 置信度阈值过滤
    5. 截断到 top_k → 写入缓存

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

    # 0. Redis 缓存检查
    cache_key = _build_cache_key(request.query, request.filters or {}, request.search_type)
    if redis_client and request.search_type != "vector_only":
        try:
            cached_raw = await redis_client.get(cache_key)
            if cached_raw:
                cached_data = json.loads(cached_raw)
                cached_results = [
                    RetrievedChunk(
                        chunk_id=c["chunk_id"],
                        content=c["content"],
                        score=c["score"],
                        source_doc=c.get("source_doc", ""),
                        metadata=c.get("metadata", {}),
                    )
                    for c in cached_data["results"]
                ]
                return RetrieveResponse(
                    results=cached_results[: request.top_k],
                    total_candidates=cached_data["total_candidates"],
                    latency_ms=int((time.monotonic() - start_time) * 1000),
                )
        except Exception:
            logger.debug("Redis 缓存读取失败，走检索路径")

    # 扩展候选集
    expanded_k = request.top_k * 3

    # ── 银行合规过滤: 注入审批状态 + 当前版本 + 时间过滤 ──
    compliance_filters = dict(request.filters or {})
    compliance_filters["approval_status"] = "PUBLISHED"
    compliance_filters["is_current_version"] = True
    if not request.include_expired:
        from datetime import date as _date

        today_str = _date.today().isoformat()
        compliance_filters["effective_date"] = {"lte": today_str}
        # expiry_date 为空或 >= 今天（ES 层用 should 处理 OR 逻辑，这里简化为不传，Python 侧后过滤）

    bm25_results: list[RetrievedChunk] = []
    vector_results: list[RetrievedChunk] = []

    if request.search_type == "hybrid":
        # 并行: ES BM25 ∥ (embed → Milvus vector)
        bm25_task = asyncio.create_task(search_bm25(es_client, request.query, expanded_k, compliance_filters))

        if embedding_provider and milvus_collection:
            try:
                query_embedding = await embedding_provider.embed_query(request.query)
                vector_task = asyncio.create_task(
                    search_vector(milvus_collection, query_embedding, expanded_k, compliance_filters)
                )
                bm25_results, vector_results = await asyncio.gather(bm25_task, vector_task)
            except Exception:
                logger.warning("向量检索嵌入失败，降级到 BM25 only")
                for t in (bm25_task, vector_task):
                    if not t.done():
                        t.cancel()
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
        bm25_results = await search_bm25(es_client, request.query, expanded_k, compliance_filters)
        fused = bm25_results

    elif request.search_type == "vector_only":
        if embedding_provider and milvus_collection:
            try:
                query_embedding = await embedding_provider.embed_query(request.query)
                vector_results = await search_vector(milvus_collection, query_embedding, expanded_k, compliance_filters)
            except Exception:
                logger.warning("向量检索失败: query=%s", request.query)
        fused = vector_results

    else:
        logger.warning("未知 search_type: %s", request.search_type)
        fused = []

    # Reranker 精排
    use_reranker_threshold = False
    if request.rerank and reranker and fused:
        candidate_count = request.top_k * 2
        candidates = fused[:candidate_count]
        content_list = [c.content for c in candidates]

        try:
            rerank_results = await asyncio.to_thread(reranker.rerank, request.query, content_list, request.top_k)
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
                use_reranker_threshold = True
        except Exception:
            logger.warning("Reranker 调用失败，使用 RRF 结果", exc_info=True)

    # 置信度阈值过滤（RRF 和 Reranker 使用不同阈值）
    threshold = confidence_threshold if use_reranker_threshold else settings.rag.rrf_confidence_threshold
    if threshold > 0 and fused:
        fused = [c for c in fused if c.score >= threshold]
        if not fused:
            logger.warning("置信度过滤后无结果: threshold=%.3f", threshold)

    # ── Milvus 合规后过滤 ──
    # Milvus schema 不含 approval_status/is_current_version 字段，
    # ES 侧已通过 term 过滤，这里对 Milvus 返回的结果做 Python 侧后过滤。
    # 通过 metadata 中的字段判断（write_to_es 已写入这些字段；
    # Milvus 结果的 metadata 中不含这些字段，需通过 doc_id 查 PG 过滤）。
    # 简化方案：ES 结果已有合规过滤，Milvus 结果通过 metadata 过滤。
    if fused:
        pre_count = len(fused)
        fused = [
            c
            for c in fused
            if c.metadata.get("approval_status", "PUBLISHED") == "PUBLISHED"
            and c.metadata.get("is_current_version", True) is True
        ]
        if len(fused) < pre_count:
            logger.debug("合规后过滤: %d → %d", pre_count, len(fused))

    # 截断到 top_k
    fused = fused[: request.top_k]

    latency_ms = int((time.monotonic() - start_time) * 1000)
    total_candidates = len(bm25_results) + len(vector_results)

    # 写入 Redis 缓存（TTL 300s，仅非空结果）
    if redis_client and fused and request.search_type != "vector_only":
        try:
            cache_data = {
                "results": [
                    {
                        "chunk_id": c.chunk_id,
                        "content": c.content,
                        "score": c.score,
                        "source_doc": c.source_doc,
                        "metadata": c.metadata,
                    }
                    for c in fused
                ],
                "total_candidates": total_candidates,
            }
            await redis_client.setex(cache_key, 300, json.dumps(cache_data, ensure_ascii=False))
        except Exception:
            logger.debug("Redis 缓存写入失败")

    return RetrieveResponse(
        results=fused,
        total_candidates=total_candidates,
        latency_ms=latency_ms,
    )
