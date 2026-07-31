"""嵌入服务抽象层

支持 Ollama 和 TEI (Text Embeddings Inference) 两种后端，
BGE-M3 为生产推荐模型。EmbeddingCircuitBreaker 提供熔断保护。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from typing import Protocol, runtime_checkable

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

BGE_QUERY_INSTRUCTION: str = "为这个句子生成表示以用于检索相关文章："


@runtime_checkable
class EmbeddingProvider(Protocol):
    """嵌入服务统一协议"""

    @property
    def dim(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def query_instruction(self) -> str: ...

    async def embed(self, texts: list[str], *, instruction: str = "") -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def health_check(self) -> bool: ...


class OllamaEmbedding:
    """Ollama 嵌入服务实现"""

    def __init__(
        self,
        base_url: str,
        model: str,
        dim: int,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dim = dim
        self._timeout = timeout

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return self._model

    @property
    def query_instruction(self) -> str:
        return BGE_QUERY_INSTRUCTION

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=0.5, max=5), reraise=True)
    async def embed(self, texts: list[str], *, instruction: str = "") -> list[list[float]]:
        if instruction:
            texts = [f"{instruction}{t}" for t in texts]
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": texts},
            )
            response.raise_for_status()
        embeddings: list[list[float]] = response.json().get("embeddings", [])
        if not embeddings:
            raise RuntimeError("嵌入服务返回空结果")
        return embeddings

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text], instruction=self.query_instruction)
        return results[0]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base_url}/api/tags")
                return response.status_code == 200
        except Exception:
            return False


class TEIEmbedding:
    """HuggingFace TEI 嵌入服务实现 (BGE-M3)

    生产推荐：TEI + BGE-M3，支持批量、int8 量化。
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        dim: int,
        batch_size: int = 128,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dim = dim
        self._batch_size = batch_size
        self._timeout = timeout

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return self._model

    @property
    def query_instruction(self) -> str:
        return BGE_QUERY_INSTRUCTION

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=0.5, max=5), reraise=True)
    async def embed(self, texts: list[str], *, instruction: str = "") -> list[list[float]]:
        if instruction:
            texts = [f"{instruction}{t}" for t in texts]

        all_embeddings: list[list[float]] = []
        num_batches = math.ceil(len(texts) / self._batch_size)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for i in range(num_batches):
                batch = texts[i * self._batch_size : (i + 1) * self._batch_size]
                response = await client.post(
                    f"{self._base_url}/embed",
                    json={"inputs": batch},
                )
                response.raise_for_status()
                batch_embeddings: list[list[float]] = response.json()
                if not batch_embeddings:
                    raise RuntimeError("嵌入服务返回空结果")
                all_embeddings.extend(batch_embeddings)

        if not all_embeddings:
            raise RuntimeError("嵌入服务返回空结果")
        return all_embeddings

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text], instruction=self.query_instruction)
        return results[0]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base_url}/health")
                return response.status_code == 200
        except Exception:
            return False


class EmbeddingCircuitBreaker:
    """嵌入服务熔断器

    周期性探测后端健康状态，连续失败达阈值后打开熔断，连续成功达阈值后关闭。
    """

    def __init__(
        self,
        provider: EmbeddingProvider,
        probe_interval: float = 30.0,
        failure_threshold: int = 3,
        recovery_threshold: int = 2,
    ) -> None:
        self._provider = provider
        self._probe_interval = probe_interval
        self._failure_threshold = failure_threshold
        self._recovery_threshold = recovery_threshold
        self._is_open = True
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._probe_task: asyncio.Task[None] | None = None

    @property
    def is_available(self) -> bool:
        return not self._is_open

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    async def start_probe(self) -> None:
        if self._probe_task is None or self._probe_task.done():
            self._probe_task = asyncio.create_task(self._probe_loop())

    async def stop_probe(self) -> None:
        if self._probe_task and not self._probe_task.done():
            self._probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._probe_task
            self._probe_task = None

    async def _probe_loop(self) -> None:
        while True:
            try:
                await self._probe_once()
            except Exception:
                logger.exception("嵌入服务探测异常")
            await asyncio.sleep(self._probe_interval)

    async def _probe_once(self) -> None:
        healthy = await self._provider.health_check()
        if healthy:
            self._consecutive_successes += 1
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            self._consecutive_successes = 0

        if not self._is_open and self._consecutive_failures >= self._failure_threshold:
            self._is_open = True
            logger.warning("嵌入服务熔断器打开：连续 %d 次探测失败", self._consecutive_failures)

        if self._is_open and self._consecutive_successes >= self._recovery_threshold:
            self._is_open = False
            logger.info("嵌入服务熔断器关闭：连续 %d 次探测成功", self._consecutive_successes)


def create_embedding_provider(
    provider_type: str,
    ollama_base_url: str = "http://localhost:11434",
    ollama_model: str = "mxbai-embed-large",
    tei_base_url: str = "http://localhost:8080",
    tei_model: str = "BAAI/bge-M3",
    dim: int = 1024,
    batch_size: int = 128,
    timeout: float = 30.0,
) -> OllamaEmbedding | TEIEmbedding:
    """嵌入服务工厂"""
    if provider_type == "ollama":
        return OllamaEmbedding(base_url=ollama_base_url, model=ollama_model, dim=dim, timeout=timeout)
    if provider_type == "tei":
        return TEIEmbedding(
            base_url=tei_base_url, model=tei_model, dim=dim, batch_size=batch_size, timeout=timeout,
        )
    raise ValueError(f"不支持的嵌入服务类型: {provider_type}")


async def embed_chunks(
    chunks: list[str],
    provider: EmbeddingProvider,
    batch_size: int = 128,
) -> list[list[float]]:
    """批量嵌入文本块"""
    all_embeddings: list[list[float]] = []
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        embeddings = await provider.embed(batch)
        all_embeddings.extend(embeddings)
    return all_embeddings
