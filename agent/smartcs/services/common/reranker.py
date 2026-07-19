"""重排序服务

提供基于 Ollama 和 TEI (Text Embeddings Inference) 的重排序能力，
用于对 RAG 检索结果进行二次排序以提升相关性。
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Protocol, runtime_checkable

import httpx

from smartcs.shared.models import RerankResult

logger = logging.getLogger(__name__)


# ── Protocol ──


@runtime_checkable
class RerankerProvider(Protocol):
    """重排序提供者协议"""

    @property
    def name(self) -> str:
        """提供者名称"""
        ...

    def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[RerankResult]:
        """对文档列表相对于查询进行重排序，返回 top_k 结果"""
        ...

    def health_check(self) -> bool:
        """检查服务是否可用"""
        ...


# ── Ollama 实现 ──


class OllamaReranker:
    """基于 Ollama /api/generate 的重排序实现

    使用 Ollama 的 generate 接口逐一对文档评分，
    按得分降序排列后返回 top_k 结果。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "bge-reranker-v2-m3",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[RerankResult]:
        """使用 Ollama /api/generate 对每篇文档并发评分并排序

        所有文档并发评分，而非逐文档串行调用。
        """
        if not documents:
            return []

        scored: list[tuple[int, float, str]] = []
        with ThreadPoolExecutor(max_workers=min(len(documents), 10)) as executor:
            futures = {executor.submit(self._score_document, query, doc): idx for idx, doc in enumerate(documents)}
            for future in as_completed(futures):
                idx = futures[future]
                score = future.result()
                scored.append((idx, score, documents[idx]))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [RerankResult(index=idx, relevance_score=score, text=text) for idx, score, text in scored[:top_k]]

    def _score_document(self, query: str, document: str) -> float:
        """调用 Ollama 对单篇文档评分"""
        prompt = (
            f"请对以下查询和文档的相关性进行评分，仅返回0到1之间的浮点数，不要返回其他内容。\n"
            f"查询：{query}\n"
            f"文档：{document}\n"
            f"相关性分数："
        )
        try:
            resp = httpx.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            response_text: str = resp.json().get("response", "")
            return self._parse_score(response_text)
        except Exception:
            logger.exception("Ollama 评分请求失败")
            return 0.0

    @staticmethod
    def _parse_score(response: str) -> float:
        """从模型响应中提取浮点数分数"""
        match = re.search(r"(\d+\.?\d*)", response.strip())
        if match:
            score = float(match.group(1))
            return max(0.0, min(1.0, score))
        return 0.0

    def health_check(self) -> bool:
        """检查 Ollama 服务是否可用"""
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=self.timeout)
            return resp.status_code == 200
        except Exception:
            logger.exception("Ollama 健康检查失败")
            return False


# ── TEI 实现 ──


class TEIReranker:
    """基于 HuggingFace TEI /rerank 的重排序实现

    TEI 提供 /rerank 端点，直接返回排序后的结果及分数。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        model: str = "BAAI/bge-reranker-v2-m3",
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"tei:{self.model}"

    def rerank(self, query: str, documents: list[str], top_k: int = 5) -> list[RerankResult]:
        """调用 TEI /rerank 端点进行重排序

        请求体包含 query、texts 和 top_n，响应为已排序的索引和分数列表。
        """
        if not documents:
            return []

        try:
            resp = httpx.post(
                f"{self.base_url}/rerank",
                json={"query": query, "texts": documents, "top_n": top_k},
                timeout=self.timeout,
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
        """检查 TEI 服务是否可用"""
        try:
            resp = httpx.get(f"{self.base_url}/health", timeout=self.timeout)
            return resp.status_code == 200
        except Exception:
            logger.exception("TEI 健康检查失败")
            return False


# ── 工厂函数 ──


def create_reranker_provider(
    provider_type: str = "ollama",
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "bge-reranker-v2-m3",
    tei_base_url: str = "http://localhost:8080",
    tei_model: str = "BAAI/bge-reranker-v2-m3",
    timeout: float = 30.0,
) -> OllamaReranker | TEIReranker:
    """创建重排序提供者实例

    Args:
        provider_type: 提供者类型，"ollama" 或 "tei"
        ollama_base_url: Ollama 服务地址
        ollama_model: Ollama 模型名称
        tei_base_url: TEI 服务地址
        tei_model: TEI 模型名称
        timeout: 请求超时时间（秒）

    Returns:
        对应类型的重排序提供者实例

    Raises:
        ValueError: 不支持的 provider_type
    """
    if provider_type == "ollama":
        return OllamaReranker(base_url=ollama_base_url, model=ollama_model, timeout=timeout)
    if provider_type == "tei":
        return TEIReranker(base_url=tei_base_url, model=tei_model, timeout=timeout)
    raise ValueError(f"不支持的重排序提供者类型: {provider_type}")
