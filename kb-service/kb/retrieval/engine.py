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

from kb.config import get_settings
from kb.circuit_breaker import GenericCircuitBreaker
from kb.logging import get_logger
from kb.middleware.prometheus import (
    RETRIEVE_COUNT,
    RETRIEVE_DEGRADATION,
    RETRIEVE_LATENCY,
)
from kb.retrieval.models import RetrievedChunk, RetrieveRequest, RetrieveResponse

logger = get_logger(__name__)

# ES keyword 过滤字段
_ES_KEYWORD_FIELDS = {
    "category", "doc_type", "card_type", "customer_tier",
    "security_level", "version", "chunk_type",
    "approval_status", "is_current_version", "doc_group",
    "model_version", "tenant_id",
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


def build_es_excludes(excludes: dict) -> list[dict]:
    """将 exclude 字典转换为 ES bool.must_not 子句列表 (I1-C2 / P2-1)

    字段值语义:
      - 单值: {"doc_type": "marketing"} → term must_not
      - list:  {"doc_type": ["marketing", "spam"]} → terms must_not
      - 任意 keyword 字段都可, 包括敏感词命中 / 营销话术

    must_not 不能单独存在, 必须配 must/should; 调用方在 retriever 构造里包裹 bool.must_not.
    """
    clauses: list[dict] = []
    for key, value in excludes.items():
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
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


def _build_cache_key(
    query: str,
    filters: dict,
    search_type: str,
    *,
    tenant_id: str = "default",
    actor_roles: list[str] | tuple[str, ...] = (),
    exclude: dict | None = None,
) -> str:
    """生成检索缓存 key (I2-C2: 含完整上下文, 防跨租户命中)

    新版 key 把 tenant_id / actor_roles / exclude 都纳入 hash, 避免:
    - alice (tenant=A) 检索结果命中 bob (tenant=B) 的缓存 (跨租户 bug)
    - 同一 query 但不同角色看到的文档集不同 (合规风险)
    - exclude 列表不同但缓存命中 (语义错误)

    注意: query 单独 hash (短 12 位), 完整上下文合并 hash (16 位), 既保证
    跨租户隔离, 又保持 Redis key 长度合理.
    """
    q_hash = hashlib.md5(query.encode()).hexdigest()[:12]  # noqa: S324
    ctx = {
        "f": filters or {},
        "t": tenant_id,
        "r": sorted(actor_roles or []),
        "x": exclude or {},
        "s": search_type,
    }
    ctx_str = json.dumps(ctx, sort_keys=True, ensure_ascii=False)
    ctx_hash = hashlib.md5(ctx_str.encode()).hexdigest()[:16]  # noqa: S324
    return f"kp:rag:cache:{search_type}:{q_hash}:{ctx_hash}"


async def _search_es_rrf(
    es_client: AsyncElasticsearch,
    query: str,
    query_embedding: list[float],
    top_k: int,
    filters: dict,
    rrf_k: int = 60,
    exclude: dict | None = None,
) -> list[RetrievedChunk]:
    """ES 原生 RRF 检索

    使用 RRF retriever 在服务端融合 BM25+IK 与 kNN，单查询完成混合检索。

    exclude (I1-C2 / P2-1): 否定语义, 转 must_not 子句
    """
    settings = get_settings()
    index_name = settings.elasticsearch.chunks_index

    filter_clauses = build_es_filters(filters)
    exclude_clauses = build_es_excludes(exclude or {})

    # BM25 standard retriever
    standard_retriever: dict[str, Any] = {
        "standard": {
            "query": {
                "match": {"content": {"query": query, "analyzer": "ik_smart"}},
            },
        },
    }
    if filter_clauses:
        bool_query: dict[str, Any] = {
            "must": [standard_retriever["standard"]["query"]],
            "filter": filter_clauses,
        }
        if exclude_clauses:
            bool_query["must_not"] = exclude_clauses
        standard_retriever["standard"]["query"] = {"bool": bool_query}

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
    if filter_clauses or exclude_clauses:
        knn_bool: dict[str, Any] = {}
        if filter_clauses:
            knn_bool["filter"] = filter_clauses
        if exclude_clauses:
            knn_bool["must_not"] = exclude_clauses
        knn_retriever["knn"]["filter"] = {"bool": knn_bool}

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
    embedding_breaker: GenericCircuitBreaker | None = None,
) -> RetrieveResponse:
    """混合检索编排 (I2-C2: 3 级降级链 + 阶段超时 + 熔断接入)

    流程:
    0. Redis 缓存命中 → 直接返回
    1. L1 hybrid: 嵌入 (受熔断保护) + ES RRF (带超时)
       ↓ 失败
    2. L2 bm25:   ES BM25 only (带超时)
       ↓ 失败
    3. L3 empty:  返空结果
    4. Reranker 精排 (rerank→no_rerank 单独标注, 不升级)
    5. 合规过滤 + 置信度阈值 + 截断
    6. 降级结果不写缓存; 正常结果写缓存 (key 含完整上下文)

    阶段超时分摊 (50/40/10):
      embed:  50% × timeout_ms
      ES:     40% × timeout_ms
      rerank: 10% × timeout_ms
    """
    import asyncio

    start_time = time.monotonic()
    settings = get_settings()
    rrf_k = request.rrf_k if request.rrf_k is not None else settings.rag.rrf_k
    confidence_threshold = settings.rag.confidence_threshold

    # I2-C2: 阶段超时预算
    timeout_ms = max(request.timeout_ms, 100)  # 最小 100ms 保护
    embed_budget_s = timeout_ms * 0.50 / 1000
    es_budget_s = timeout_ms * 0.40 / 1000
    rerank_budget_s = timeout_ms * 0.10 / 1000

    # 收集降级轨迹 (用于响应 degraded_stages + Prometheus 计数)
    degraded_stages: list[str] = []

    # 0. 缓存检查 (key 含完整上下文, 防跨租户命中)
    cache_key = _build_cache_key(
        request.query, request.filters or {}, request.search_type,
        tenant_id=request.tenant_id or "default",
        actor_roles=request.actor_roles or (),
        exclude=request.exclude or {},
    )
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

    # 银行合规过滤 (I1-C2: 注入 tenant_id + allowed_roles)
    compliance_filters = dict(request.filters or {})
    compliance_filters["approval_status"] = "PUBLISHED"
    compliance_filters["is_current_version"] = True
    if not request.include_expired:
        today_str = date_cls.today().isoformat()
        compliance_filters["effective_date"] = {"lte": today_str}

    # ── I1-C2: 多租户隔离 ──
    if request.tenant_id is not None:
        compliance_filters["tenant_id"] = request.tenant_id

    # ── I1-C2: 角色匹配 ──
    if request.actor_roles:
        compliance_filters["allowed_roles"] = request.actor_roles

    # 影子索引灰度
    if settings.rag.shadow_model_version:
        compliance_filters["model_version"] = settings.rag.shadow_model_version
    elif request.model_version:
        compliance_filters["model_version"] = request.model_version

    # ── I1-C2: 排除语义 (must_not) ──
    exclude = dict(request.exclude or {})

    fused: list[RetrievedChunk] = []

    # ── I2-C2: 3 级降级链 (L1 hybrid → L2 bm25 → L3 empty) ──
    if request.search_type == "hybrid":
        fused, stage_fell_back = await _try_l1_hybrid(
            request=request,
            es_client=es_client,
            embedding_provider=embedding_provider,
            embedding_breaker=embedding_breaker,
            expanded_k=expanded_k,
            compliance_filters=compliance_filters,
            rrf_k=rrf_k,
            exclude=exclude,
            embed_budget_s=embed_budget_s,
            es_budget_s=es_budget_s,
        )
        if stage_fell_back:
            degraded_stages.append(stage_fell_back)

            # L2: BM25 only
            if es_client:
                l2_result, l2_fell_back = await _try_l2_bm25(
                    es_client=es_client,
                    query=request.query,
                    expanded_k=expanded_k,
                    compliance_filters=compliance_filters,
                    es_budget_s=es_budget_s,
                )
                fused = l2_result
                if l2_fell_back:
                    degraded_stages.append(l2_fell_back)
                    # L3: empty (不调用 ES, 直接返空)
                    fused = []
                    logger.warning(
                        "L3 降级: hybrid → bm25 → empty (timeout/异常累计)",
                        query_len=len(request.query),
                    )
                else:
                    logger.info("L1 失败, L2 bm25 成功", query_len=len(request.query))
            else:
                # L1 失败 + 没有 ES, 直接 L3
                degraded_stages.append("hybrid→empty")
                fused = []
                logger.warning("L3 降级: 无 ES 客户端", query_len=len(request.query))

    elif request.search_type == "bm25_only":
        if es_client:
            l2_result, l2_fell_back = await _try_l2_bm25(
                es_client=es_client,
                query=request.query,
                expanded_k=expanded_k,
                compliance_filters=compliance_filters,
                es_budget_s=es_budget_s,
            )
            fused = l2_result
            if l2_fell_back:
                degraded_stages.append(l2_fell_back)
                fused = []
        else:
            fused = []

    elif request.search_type == "vector_only":
        # vector_only 本身就是降级路径, 不再 L1→L2 链
        if es_client and embedding_provider:
            try:
                if embedding_breaker and not embedding_breaker.is_available:
                    raise RuntimeError("embedding_breaker_open")
                query_embedding = await asyncio.wait_for(
                    embedding_provider.embed_query(request.query),
                    timeout=embed_budget_s,
                )
                if embedding_breaker:
                    await embedding_breaker.record_success()
            except (asyncio.TimeoutError, Exception) as e:
                if embedding_breaker:
                    await embedding_breaker.record_failure()
                logger.warning(
                    "vector_only 嵌入失败: %s: %s", type(e).__name__, e,
                )
                degraded_stages.append("vector_only→empty")
                fused = []
            else:
                try:
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
                    resp = await asyncio.wait_for(
                        es_client.search(index=index_name, knn=knn_body, size=expanded_k),
                        timeout=es_budget_s,
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
                except (asyncio.TimeoutError, Exception) as e:
                    logger.warning(
                        "vector_only ES kNN 失败: %s: %s", type(e).__name__, e,
                    )
                    degraded_stages.append("vector_only→empty")
                    fused = []

    # Reranker 精排 (rerank→no_rerank 单独标注, 不升级 degraded)
    if request.rerank and reranker and fused:
        candidates = fused[: request.top_k * 2]
        content_list = [c.content for c in candidates]
        try:
            rerank_results = await asyncio.wait_for(
                asyncio.to_thread(reranker.rerank, request.query, content_list, request.top_k),
                timeout=rerank_budget_s,
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
        except (asyncio.TimeoutError, Exception) as e:
            # rerank 失败不视为通道降级 (增强非必需)
            degraded_stages.append("rerank→no_rerank")
            logger.warning(
                "Reranker 调用失败, 使用 RRF 结果: %s: %s",
                type(e).__name__, e,
            )

    # 置信度过滤
    if confidence_threshold > 0 and fused:
        fused = [c for c in fused if c.score >= confidence_threshold]

    # 截断
    fused = fused[: request.top_k]
    latency_ms = int((time.monotonic() - start_time) * 1000)

    # I2-C2: 降级结果不写缓存 (避免污染正常缓存池)
    is_degraded = bool(degraded_stages)
    if redis_client and fused and request.search_type != "vector_only" and not is_degraded:
        try:
            cache_data = {
                "results": [c.model_dump() for c in fused],
                "total_candidates": len(fused),
            }
            await redis_client.setex(cache_key, 300, json.dumps(cache_data, ensure_ascii=False))
        except Exception:
            logger.debug("Redis 缓存写入失败")

    # I2-C2: Prometheus 指标 + 降级计数
    try:
        RETRIEVE_COUNT.labels(
            search_type=request.search_type,
            status="success" if not is_degraded else "degraded",
        ).inc()
        RETRIEVE_LATENCY.labels(search_type=request.search_type).observe(
            (time.monotonic() - start_time),
        )
        for stage in degraded_stages:
            if "→" in stage:
                from_s, to_s = stage.split("→", 1)
                RETRIEVE_DEGRADATION.labels(from_=from_s, to=to_s).inc()
    except Exception:
        logger.debug("Prometheus 指标记录失败")

    return RetrieveResponse(
        results=fused,
        total_candidates=len(fused),
        latency_ms=latency_ms,
        degraded=is_degraded,
        degraded_stages=degraded_stages,
    )


async def _try_l1_hybrid(
    *,
    request: RetrieveRequest,
    es_client: AsyncElasticsearch | None,
    embedding_provider: Any | None,
    embedding_breaker: GenericCircuitBreaker | None,
    expanded_k: int,
    compliance_filters: dict,
    rrf_k: int,
    exclude: dict,
    embed_budget_s: float,
    es_budget_s: float,
) -> tuple[list[RetrievedChunk], str | None]:
    """L1 hybrid 尝试 — 嵌入 + ES RRF

    Returns:
        (fused, fell_back_to_stage): 成功时 fell_back=None, 失败时为 'hybrid→bm25' / 'hybrid→empty'
    """
    import asyncio

    if es_client is None or embedding_provider is None:
        logger.warning("L1 hybrid 需要 ES + embedding_provider, 跳过 L1")
        return [], "hybrid→bm25"

    # I2-C2: 熔断器检查 (breaker open → 跳过 embed, 直接走 L2)
    if embedding_breaker and not embedding_breaker.is_available:
        logger.info("熔断器打开, 跳过 L1 embed → L2 bm25", breaker=embedding_breaker.name)
        return [], "hybrid→bm25"

    try:
        query_embedding = await asyncio.wait_for(
            embedding_provider.embed_query(request.query),
            timeout=embed_budget_s,
        )
        if embedding_breaker:
            await embedding_breaker.record_success()
    except (asyncio.TimeoutError, Exception) as e:
        if embedding_breaker:
            await embedding_breaker.record_failure()
        logger.warning(
            "L1 embed 失败: %s: %s (%.0fms 超时)",
            type(e).__name__, e, embed_budget_s * 1000,
        )
        return [], "hybrid→bm25"

    try:
        fused = await _search_es_rrf(
            es_client, request.query, query_embedding,
            expanded_k, compliance_filters, rrf_k, exclude,
        )
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(
            "L1 ES RRF 失败: %s: %s (%.0fms 超时)",
            type(e).__name__, e, es_budget_s * 1000,
        )
        return [], "hybrid→bm25"

    return fused, None


async def _try_l2_bm25(
    *,
    es_client: AsyncElasticsearch,
    query: str,
    expanded_k: int,
    compliance_filters: dict,
    es_budget_s: float,
) -> tuple[list[RetrievedChunk], str | None]:
    """L2 bm25 尝试 — ES BM25 only

    Returns:
        (fused, fell_back_to_stage): 成功时 fell_back=None, 失败时为 'bm25→empty'
    """
    import asyncio

    try:
        fused = await _search_bm25_only(
            es_client, query, expanded_k, compliance_filters,
        )
        return fused, None
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(
            "L2 bm25 失败: %s: %s (%.0fms 超时)",
            type(e).__name__, e, es_budget_s * 1000,
        )
        return [], "bm25→empty"
