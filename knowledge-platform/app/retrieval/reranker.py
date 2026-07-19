"""重排序服务

BGE-reranker-v2-m3 via TEI，对 RRF 候选集精排 top-50→top-10。
生产推荐：TEI /rerank 端点，交叉编码器精度远高于双塔向量。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import httpx

from app.retrieval.models import RerankResult

logger = logging.getLogger(__name__)


@runtime_checkable
class RerankerProvider(Protocol):
    """重排序提供者协议"""

    @property
    def name(self) -> str: ...

    def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[RerankResult]: ...

    def health_check(self) -> bool: ...


class TEIReranker:
    """TEI /rerank 重排序实现 (BGE-reranker-v2-m3)

    生产推荐：TEI 专用重排服务，交叉编码器精度最优。
    """

    def __init__(self, base_url: str, model: str = "BAAI/bge-reranker-v2-m3", timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    @property
    def name(self) -> str:
        return f"tei:{self._model}"

    def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[RerankResult]:
        """调用 TEI /rerank 端点精排"""
        if not documents:
            return []

        try:
            resp = httpx.post(
                f"{self._base_url}/rerank",
                json={"query": query, "texts": documents, "top_n": top_k},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data: list[dict] = resp.json()
            return [
                RerankResult(
                    index=item["index"],
                    relevance_score=item["relevance_score"],
                    text=documents[item["index"]] if item["index"] < len(documents) else "",
                )
                for item in data
            ]
        except Exception:
            logger.exception("TEI 重排序请求失败")
            return []

    def health_check(self) -> bool:
        try:
            resp = httpx.get(f"{self._base_url}/health", timeout=self._timeout)
            return resp.status_code == 200
        except Exception:
            return False


class OllamaReranker:
    """Ollama /api/generate 重排序实现（开发环境回退）"""

    def __init__(self, base_url: str, model: str = "bge-reranker-v2-m3", timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    @property
    def name(self) -> str:
        return f"ollama:{self._model}"

    def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[RerankResult]:
        """使用 Ollama generate 逐文档评分"""
        import re
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not documents:
            return []

        scored: list[tuple[int, float]] = []
        with ThreadPoolExecutor(max_workers=min(len(documents), 10)) as executor:
            futures = {
                executor.submit(self._score_document, query, doc): idx
                for idx, doc in enumerate(documents)
            }
            for future in as_completed(futures):
                idx = futures[future]
                score = future.result()
                scored.append((idx, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            RerankResult(index=idx, relevance_score=score, text=documents[idx])
            for idx, score in scored[:top_k]
        ]

    def _score_document(self, query: str, document: str) -> float:
        prompt = (
            f"请对以下查询和文档的相关性进行评分，仅返回0到1之间的浮点数。\n"
            f"查询：{query}\n文档：{document}\n相关性分数："
        )
        try:
            resp = httpx.post(
                f"{self._base_url}/api/generate",
                json={"model": self._model, "prompt": prompt, "stream": False},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            text: str = resp.json().get("response", "")
            match = re.search(r"(\d+\.?\d*)", text.strip())
            if match:
                return max(0.0, min(1.0, float(match.group(1))))
            return 0.0
        except Exception:
            return 0.0

    def health_check(self) -> bool:
        try:
            resp = httpx.get(f"{self._base_url}/api/tags", timeout=self._timeout)
            return resp.status_code == 200
        except Exception:
            return False


def create_reranker_provider(
    provider_type: str = "tei",
    tei_base_url: str = "http://localhost:8083",
    tei_model: str = "BAAI/bge-reranker-v2-m3",
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "bge-reranker-v2-m3",
    timeout: float = 10.0,
) -> TEIReranker | OllamaReranker:
    """重排序服务工厂"""
    if provider_type == "tei":
        return TEIReranker(base_url=tei_base_url, model=tei_model, timeout=timeout)
    if provider_type == "ollama":
        return OllamaReranker(base_url=ollama_base_url, model=ollama_model, timeout=timeout)
    raise ValueError(f"不支持的重排序提供者类型: {provider_type}")
