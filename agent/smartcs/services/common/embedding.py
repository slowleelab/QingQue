"""嵌入服务抽象层

支持 Ollama 和 TEI (Text Embeddings Inference) 两种嵌入后端，
通过 EmbeddingProvider Protocol 统一接口，EmbeddingCircuitBreaker 提供熔断保护。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from typing import Protocol, runtime_checkable

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from smartcs.shared.exceptions import EmbeddingServiceError, EmbeddingTimeoutError

logger = logging.getLogger(__name__)

BGE_QUERY_INSTRUCTION: str = "为这个句子生成表示以用于检索相关文章："


@runtime_checkable
class EmbeddingProvider(Protocol):
    """嵌入服务统一协议"""

    @property
    def dim(self) -> int:
        """嵌入向量维度"""
        ...

    @property
    def name(self) -> str:
        """模型名称"""
        ...

    @property
    def query_instruction(self) -> str:
        """查询嵌入指令前缀"""
        ...

    async def embed(self, texts: list[str], *, instruction: str = "") -> list[list[float]]:
        """将文本列表转换为嵌入向量列表

        Args:
            texts: 待嵌入文本列表
            instruction: 可选指令前缀，非空时添加到每个文本前面
        """
        ...

    async def embed_query(self, text: str) -> list[float]:
        """将单条查询文本转换为嵌入向量，自动添加查询指令"""
        ...

    async def health_check(self) -> bool:
        """检查嵌入服务是否可用"""
        ...


class OllamaEmbedding:
    """Ollama 嵌入服务实现

    通过 Ollama /api/embed 接口获取嵌入向量。
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        dim: int,
        timeout: float = 10.0,
        max_retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dim = dim
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return self._model

    @property
    def query_instruction(self) -> str:
        return BGE_QUERY_INSTRUCTION

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=0.5, max=5),
        reraise=True,
    )
    async def embed(self, texts: list[str], *, instruction: str = "") -> list[list[float]]:
        """调用 Ollama /api/embed 获取嵌入向量"""
        if instruction:
            texts = [f"{instruction}{t}" for t in texts]

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/api/embed",
                    json={"model": self._model, "input": texts},
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise EmbeddingTimeoutError(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise EmbeddingServiceError(str(exc)) from exc

        data = response.json()
        embeddings: list[list[float]] = data.get("embeddings", [])
        if not embeddings:
            raise EmbeddingServiceError("嵌入服务返回空结果")

        return embeddings

    async def embed_query(self, text: str) -> list[float]:
        """嵌入查询文本，自动添加 BGE 查询指令"""
        results = await self.embed([text], instruction=self.query_instruction)
        return results[0]

    async def health_check(self) -> bool:
        """通过 GET /api/tags 检查 Ollama 服务可用性"""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base_url}/api/tags")
                return response.status_code == 200
        except Exception:
            return False


class TEIEmbedding:
    """HuggingFace Text Embeddings Inference (TEI) 嵌入服务实现

    通过 TEI /embed 接口获取嵌入向量，支持批量处理。
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        dim: int,
        batch_size: int = 128,
        timeout: float = 10.0,
        max_retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dim = dim
        self._batch_size = batch_size
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return self._model

    @property
    def query_instruction(self) -> str:
        return BGE_QUERY_INSTRUCTION

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=0.5, max=5),
        reraise=True,
    )
    async def embed(self, texts: list[str], *, instruction: str = "") -> list[list[float]]:
        """调用 TEI /embed 获取嵌入向量，按 batch_size 分批请求"""
        if instruction:
            texts = [f"{instruction}{t}" for t in texts]

        all_embeddings: list[list[float]] = []
        num_batches = math.ceil(len(texts) / self._batch_size)

        try:
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
                        raise EmbeddingServiceError("嵌入服务返回空结果")
                    all_embeddings.extend(batch_embeddings)
        except httpx.TimeoutException as exc:
            raise EmbeddingTimeoutError(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise EmbeddingServiceError(str(exc)) from exc

        if not all_embeddings:
            raise EmbeddingServiceError("嵌入服务返回空结果")

        return all_embeddings

    async def embed_query(self, text: str) -> list[float]:
        """嵌入查询文本，自动添加 BGE 查询指令"""
        results = await self.embed([text], instruction=self.query_instruction)
        return results[0]

    async def health_check(self) -> bool:
        """通过 GET /health 检查 TEI 服务可用性"""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base_url}/health")
                return response.status_code == 200
        except Exception:
            return False


class EmbeddingCircuitBreaker:
    """嵌入服务熔断器

    周期性探测后端健康状态，连续失败达到阈值后打开熔断（标记不可用），
    连续成功达到阈值后关闭熔断（恢复可用）。
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
        self._is_open = True  # 初始假定不可用，首次探测成功后关闭
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._probe_task: asyncio.Task[None] | None = None

    @property
    def is_available(self) -> bool:
        """熔断器是否闭合（服务可用）"""
        return not self._is_open

    @property
    def provider(self) -> EmbeddingProvider:
        """底层嵌入提供者"""
        return self._provider

    async def start_probe(self) -> None:
        """启动周期性探测任务"""
        if self._probe_task is None or self._probe_task.done():
            self._probe_task = asyncio.create_task(self._probe_loop())

    async def stop_probe(self) -> None:
        """停止周期性探测任务"""
        if self._probe_task and not self._probe_task.done():
            self._probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._probe_task
            self._probe_task = None

    async def _probe_loop(self) -> None:
        """周期性执行健康探测"""
        while True:
            try:
                await self._probe_once()
            except Exception:
                logger.exception("嵌入服务探测异常")
            await asyncio.sleep(self._probe_interval)

    async def _probe_once(self) -> None:
        """执行一次健康探测并更新熔断状态"""
        healthy = await self._provider.health_check()

        if healthy:
            self._consecutive_successes += 1
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            self._consecutive_successes = 0

        # 熔断打开条件：连续失败达到阈值
        if not self._is_open and self._consecutive_failures >= self._failure_threshold:
            self._is_open = True
            logger.warning("嵌入服务熔断器打开：连续 %d 次探测失败", self._consecutive_failures)

        # 熔断关闭条件：连续成功达到阈值
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
    timeout: float = 10.0,
    max_retries: int = 2,
) -> OllamaEmbedding | TEIEmbedding:
    """嵌入服务工厂函数

    根据配置创建对应的嵌入服务实例。

    Args:
        provider_type: 提供者类型，"ollama" 或 "tei"
        ollama_base_url: Ollama 服务地址
        ollama_model: Ollama 模型名称
        tei_base_url: TEI 服务地址
        tei_model: TEI 模型名称
        dim: 嵌入向量维度
        batch_size: TEI 批量大小
        timeout: 请求超时（秒）
        max_retries: 最大重试次数
    """
    if provider_type == "ollama":
        return OllamaEmbedding(
            base_url=ollama_base_url,
            model=ollama_model,
            dim=dim,
            timeout=timeout,
            max_retries=max_retries,
        )
    if provider_type == "tei":
        return TEIEmbedding(
            base_url=tei_base_url,
            model=tei_model,
            dim=dim,
            batch_size=batch_size,
            timeout=timeout,
            max_retries=max_retries,
        )
    raise EmbeddingServiceError(f"不支持的嵌入服务类型: {provider_type}")
