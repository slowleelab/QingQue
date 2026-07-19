"""RAGAS 检索质量评估

golden query 集 + NDCG/MRR 评估，作为分块/嵌入/检索改动的回归门禁。

使用方式: python -m app.eval.ragas_eval
"""

from __future__ import annotations

import asyncio
import logging
import math

logger = logging.getLogger(__name__)

# Golden query 集 — 银行信用卡客服典型问题
GOLDEN_QUERIES: list[dict] = [
    {"query": "信用卡年费怎么减免", "expected_keywords": ["年费", "减免", "消费"], "expected_category": "年费"},
    {"query": "信用卡积分如何兑换", "expected_keywords": ["积分", "兑换"], "expected_category": "积分"},
    {"query": "信用卡丢失了怎么办", "expected_keywords": ["挂失", "补卡"], "expected_category": "安全"},
    {"query": "信用卡还款方式有哪些", "expected_keywords": ["还款", "方式"], "expected_category": "还款"},
    {"query": "信用卡分期手续费多少", "expected_keywords": ["分期", "手续费", "费率"], "expected_category": "费率"},
    {"query": "信用卡章程有什么规定", "expected_keywords": ["章程", "规定"], "expected_category": "章程"},
    {"query": "信用卡有哪些活动", "expected_keywords": ["活动", "优惠"], "expected_category": "活动"},
    {"query": "信用卡账单日和还款日", "expected_keywords": ["账单日", "还款日"], "expected_category": "还款"},
]


def compute_mrr(results: list[dict], expected_keywords: list[str]) -> float:
    """MRR: 第一个命中结果的 1/rank"""
    for rank, r in enumerate(results, start=1):
        content = r.get("content", "")
        if any(kw in content for kw in expected_keywords):
            return 1.0 / rank
    return 0.0


def compute_ndcg(results: list[dict], expected_keywords: list[str], k: int = 5) -> float:
    """NDCG@k: 包含期望关键词=1，否则=0"""
    dcg = 0.0
    for i, r in enumerate(results[:k], start=1):
        content = r.get("content", "")
        rel = 1 if any(kw in content for kw in expected_keywords) else 0
        dcg += rel / math.log2(i + 1)

    ideal_hits = min(len(results), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def compute_category_hit_rate(results: list[dict], expected_category: str) -> float:
    """分类命中率"""
    if not results:
        return 0.0
    hits = sum(1 for r in results if r.get("metadata", {}).get("category") == expected_category)
    return hits / len(results)


async def run_evaluation() -> dict:
    """运行评估"""
    from app.config import get_settings
    from app.pipeline.embedder import create_embedding_provider
    from app.retrieval.engine import retrieve
    from app.retrieval.models import RetrieveRequest
    from app.storage.elasticsearch import get_es
    from app.storage.redis import get_redis

    es_client = get_es()
    redis_client = get_redis()

    settings = get_settings()
    ollama_base = settings.llm.base_url.replace("/v1", "").rstrip("/")
    embedding_provider = create_embedding_provider(
        provider_type=settings.rag.embedding_provider,
        ollama_base_url=ollama_base,
        ollama_model=settings.rag.embedding_model,
        tei_base_url=settings.rag.tei_base_url,
        tei_model=settings.rag.embedding_model,
        dim=settings.rag.embedding_dim,
    )

    total_mrr = 0.0
    total_ndcg = 0.0
    total_cat = 0.0
    detail: list[dict] = []

    for golden in GOLDEN_QUERIES:
        request = RetrieveRequest(query=golden["query"], top_k=5, search_type="hybrid", rerank=False)
        response = await retrieve(
            request=request, es_client=es_client,
            embedding_provider=embedding_provider, reranker=None, redis_client=redis_client,
        )

        results = [r.model_dump() for r in response.results]
        mrr = compute_mrr(results, golden["expected_keywords"])
        ndcg = compute_ndcg(results, golden["expected_keywords"])
        cat = compute_category_hit_rate(results, golden["expected_category"])

        total_mrr += mrr
        total_ndcg += ndcg
        total_cat += cat

        detail.append({
            "query": golden["query"], "mrr": round(mrr, 4),
            "ndcg": round(ndcg, 4), "cat_hit": round(cat, 4),
            "latency_ms": response.latency_ms, "result_count": len(results),
        })

        logger.info("Q: %s | MRR=%.3f NDCG=%.3f Cat=%.3f | %dms",
                     golden["query"], mrr, ndcg, cat, response.latency_ms)

    n = len(GOLDEN_QUERIES)
    report = {
        "total_queries": n,
        "avg_mrr": round(total_mrr / n, 4),
        "avg_ndcg": round(total_ndcg / n, 4),
        "avg_category_hit": round(total_cat / n, 4),
        "details": detail,
    }

    print("\n" + "=" * 60)
    print("RAGAS 评估报告")
    print("=" * 60)
    print(f"查询数: {report['total_queries']}")
    print(f"平均 MRR:       {report['avg_mrr']}")
    print(f"平均 NDCG@5:    {report['avg_ndcg']}")
    print(f"平均分类命中率: {report['avg_category_hit']}")
    print("=" * 60)
    for d in detail:
        print(f"  {d['query'][:20]:<22} MRR={d['mrr']:.3f} NDCG={d['ndcg']:.3f} Cat={d['cat_hit']:.3f}")

    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    asyncio.run(run_evaluation())
